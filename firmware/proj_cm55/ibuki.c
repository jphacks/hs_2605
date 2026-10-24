/* IBUKI firmware: per-chirp range FFT and the C/D/S/B/L line protocol over the debug UART.
 *
 * Per frame: for each chirp of one RX antenna, subtract the chirp mean (DC), apply a Hann
 * window, take a 64-point real FFT, and add the complex bins 8..31 to an accumulator.
 * Every IBUKI_FRAMES_PER_LINE frames the accumulator is averaged and sent as one D line.
 */
#include "ibuki.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cybsp.h"
#include "arm_math.h"

#define TX_LINE_MAX    (512U)
#define RX_LINE_MAX (16U)
#define BTN_DEBOUNCE_MS (30U)
#define TEST_STALL_MAX_MS (2000U)
#define LED_BLINK_HALF_MS (250U) /* alert LED: 2 blinks per second */

static arm_rfft_fast_instance_f32 rfft;
static float hann[IBUKI_SAMPLES_PER_CHIRP];
static float chirp_buf[IBUKI_SAMPLES_PER_CHIRP];
static float spec_buf[IBUKI_SAMPLES_PER_CHIRP];
static float acc_re[IBUKI_NUM_BINS];
static float acc_im[IBUKI_NUM_BINS];
static uint32_t frames_in_acc;
static uint32_t d_seq;
static uint32_t last_config_ms;
static char line[TX_LINE_MAX];
static char rx_line[RX_LINE_MAX];
static uint32_t rx_len;
static bool restart_requested;
static bool btn_stable_pressed;
static bool btn_last_raw;
static uint32_t btn_change_ms;

/* time */
static volatile uint32_t ms_ticks;

void SysTick_Handler(void)
{
    ms_ticks++;
}

uint32_t ibuki_millis(void)
{
    return ms_ticks;
}

/* text output */
static char* put_u32(char* p, uint32_t v)
{
    char tmp[10];
    int n = 0;
    do
    {
        tmp[n++] = (char)('0' + (v % 10U));
        v /= 10U;
    } while (v != 0U);
    while (n > 0)
    {
        *p++ = tmp[--n];
    }
    return p;
}

static char* put_i32(char* p, int32_t v)
{
    if (v < 0)
    {
        *p++ = '-';
        return put_u32(p, (uint32_t)(-(int64_t)v));
    }
    return put_u32(p, (uint32_t)v);
}

/* Fixed-point value with one decimal, from tenths: 375 -> "37.5". */
static char* put_tenths(char* p, uint32_t tenths)
{
    p = put_u32(p, tenths / 10U);
    *p++ = '.';
    *p++ = (char)('0' + (tenths % 10U));
    return p;
}

static void send_line(char* end)
{
    *end++ = '\n';
    *end = '\0';
    fputs(line, stdout);
    fflush(stdout);
}

void ibuki_status(const char* text)
{
    char* p = line;
    *p++ = 'S';
    *p++ = ',';
    p = put_u32(p, ibuki_millis());
    *p++ = ',';
    size_t n = strlen(text);
    if (n > TX_LINE_MAX - 32U)
    {
        n = TX_LINE_MAX - 32U;
    }
    memcpy(p, text, n);
    send_line(p + n);
}

void ibuki_status_code(const char* text, uint32_t code)
{
    char buf[96];
    snprintf(buf, sizeof(buf), "%s 0x%08lx", text, (unsigned long)code);
    ibuki_status(buf);
}

/* C,first_cm,step_mm,n,scale */
static void send_config(void)
{
    char* p = line;
    *p++ = 'C';
    *p++ = ',';
    p = put_tenths(p, (IBUKI_FIRST_BIN * IBUKI_BIN_STEP_UM) / 1000U); /* cm in tenths */
    *p++ = ',';
    p = put_tenths(p, IBUKI_BIN_STEP_UM / 100U);                       /* mm in tenths */
    *p++ = ',';
    p = put_u32(p, IBUKI_NUM_BINS);
    *p++ = ',';
    p = put_u32(p, (uint32_t)IBUKI_SCALE);
    send_line(p);
}

/* D,seq,t_ms,I0,Q0,...,In-1,Qn-1 */
static void send_data(void)
{
    const float k = (float)IBUKI_SCALE / (float)(frames_in_acc * IBUKI_CHIRPS_PER_FRAME);
    char* p = line;
    *p++ = 'D';
    *p++ = ',';
    p = put_u32(p, d_seq++);
    *p++ = ',';
    p = put_u32(p, ibuki_millis());
    for (uint32_t b = 0U; b < IBUKI_NUM_BINS; b++)
    {
        *p++ = ',';
        p = put_i32(p, (int32_t)lrintf(acc_re[b] * k));
        *p++ = ',';
        p = put_i32(p, (int32_t)lrintf(acc_im[b] * k));
    }
    send_line(p);
}

/* LED and button */
static bool alert_on;          /* PC sent L,1: the red LED blinks so it stands out from the */
static uint32_t blink_last_ms; /* board's other red lights (power, KitProg) */
static bool blink_lit;

static void led_red(bool on)
{
    Cy_GPIO_Write(CYBSP_LED_RGB_RED_PORT, CYBSP_LED_RGB_RED_PIN,
                  on ? CYBSP_LED_STATE_ON : CYBSP_LED_STATE_OFF);
}

static void set_alert(bool on)
{
    alert_on = on;
    blink_lit = on;
    blink_last_ms = ibuki_millis();
    led_red(on);
}

static void poll_blink(uint32_t now)
{
    if (alert_on && (now - blink_last_ms) >= LED_BLINK_HALF_MS)
    {
        blink_last_ms = now;
        blink_lit = !blink_lit;
        led_red(blink_lit);
    }
}

static bool button_raw_pressed(void)
{
    return Cy_GPIO_Read(CYBSP_USER_BTN_PORT, CYBSP_USER_BTN_PIN) == CYBSP_BTN_PRESSED;
}

static void handle_rx_line(void)
{
    if (strcmp(rx_line, "L,1") == 0)
    {
        set_alert(true);
    }
    else if (strcmp(rx_line, "L,0") == 0)
    {
        set_alert(false);
    }
    else if (strcmp(rx_line, "R,1") == 0)
    {
        restart_requested = true; /* main loop restarts the radar */
    }
    else if ((rx_line[0] == 'T') && (rx_line[1] == ','))
    {
        /* Test only: stall the main loop so the radar FIFO overflows, to check the recovery. */
        uint32_t ms = (uint32_t)strtoul(&rx_line[2], NULL, 10);
        if (ms > TEST_STALL_MAX_MS)
        {
            ms = TEST_STALL_MAX_MS;
        }
        ibuki_status_code("test stall ms", ms);
        Cy_SysLib_Delay(ms);
    }
}

bool ibuki_take_restart_request(void)
{
    bool requested = restart_requested;
    restart_requested = false;
    return requested;
}

static void poll_uart_rx(void)
{
    while (Cy_SCB_UART_GetNumInRxFifo(CYBSP_DEBUG_UART_HW) > 0U)
    {
        char ch = (char)Cy_SCB_UART_Get(CYBSP_DEBUG_UART_HW);
        if ((ch == '\n') || (ch == '\r'))
        {
            rx_line[rx_len] = '\0';
            if (rx_len > 0U)
            {
                handle_rx_line();
            }
            rx_len = 0U;
        }
        else if (rx_len < (RX_LINE_MAX - 1U))
        {
            rx_line[rx_len++] = ch;
        }
        else
        {
            rx_len = 0U; /* too long: not one of our commands, drop it */
        }
    }
}

static void poll_button(uint32_t now)
{
    bool raw = button_raw_pressed();
    if (raw != btn_last_raw)
    {
        btn_last_raw = raw;
        btn_change_ms = now;
    }
    else if ((raw != btn_stable_pressed) && ((now - btn_change_ms) >= BTN_DEBOUNCE_MS))
    {
        btn_stable_pressed = raw;
        if (raw)
        {
            char* p = line;
            *p++ = 'B';
            *p++ = ',';
            *p++ = '1';
            send_line(p);
        }
    }
}

/* public */
void ibuki_init(void)
{
    SysTick_Config(SystemCoreClock / 1000U);

    for (uint32_t n = 0U; n < IBUKI_SAMPLES_PER_CHIRP; n++)
    {
        hann[n] = 0.5f * (1.0f - cosf(2.0f * PI * (float)n / (float)(IBUKI_SAMPLES_PER_CHIRP - 1U)));
    }
    if (arm_rfft_fast_init_f32(&rfft, IBUKI_SAMPLES_PER_CHIRP) != ARM_MATH_SUCCESS)
    {
        ibuki_status("rfft init failed");
    }

    memset(acc_re, 0, sizeof(acc_re));
    memset(acc_im, 0, sizeof(acc_im));
    frames_in_acc = 0U;
    d_seq = 0U;
    rx_len = 0U;
    btn_last_raw = button_raw_pressed();
    btn_stable_pressed = btn_last_raw;
    btn_change_ms = 0U;
    set_alert(false);

    ibuki_status("ibuki fw start");
    send_config();
    last_config_ms = ibuki_millis();
}

void ibuki_on_frame(const float* frame)
{
    const uint32_t stride = IBUKI_SAMPLES_PER_CHIRP * IBUKI_RX_ANTENNAS;

    for (uint32_t c = 0U; c < IBUKI_CHIRPS_PER_FRAME; c++)
    {
        const float* chirp = frame + (c * stride) + IBUKI_RX_USED;

        float mean = 0.0f;
        for (uint32_t s = 0U; s < IBUKI_SAMPLES_PER_CHIRP; s++)
        {
            mean += chirp[s * IBUKI_RX_ANTENNAS];
        }
        mean /= (float)IBUKI_SAMPLES_PER_CHIRP;

        for (uint32_t s = 0U; s < IBUKI_SAMPLES_PER_CHIRP; s++)
        {
            chirp_buf[s] = (chirp[s * IBUKI_RX_ANTENNAS] - mean) * hann[s];
        }

        /* Output packing: [Re0, Re(N/2), Re1, Im1, Re2, Im2, ...]. arm_rfft_fast_f32 uses
         * chirp_buf as scratch, which is fine because it is refilled for every chirp. */
        arm_rfft_fast_f32(&rfft, chirp_buf, spec_buf, 0U);

        for (uint32_t b = 0U; b < IBUKI_NUM_BINS; b++)
        {
            uint32_t k = IBUKI_FIRST_BIN + b;
            acc_re[b] += spec_buf[2U * k];
            acc_im[b] += spec_buf[(2U * k) + 1U];
        }
    }

    frames_in_acc++;
    if (frames_in_acc >= IBUKI_FRAMES_PER_LINE)
    {
        send_data();
        memset(acc_re, 0, sizeof(acc_re));
        memset(acc_im, 0, sizeof(acc_im));
        frames_in_acc = 0U;
    }
}

void ibuki_reset_frames(void)
{
    memset(acc_re, 0, sizeof(acc_re));
    memset(acc_im, 0, sizeof(acc_im));
    frames_in_acc = 0U;
}

void ibuki_poll(void)
{
    uint32_t now = ibuki_millis();
    if ((now - last_config_ms) >= IBUKI_CONFIG_PERIOD_MS)
    {
        send_config();
        last_config_ms = now;
    }
    poll_uart_rx();
    poll_button(now);
    poll_blink(now);
}
