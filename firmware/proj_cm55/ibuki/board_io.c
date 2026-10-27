/* Alert LED, user button and commands from the PC (see board_io.h). */
#include "board_io.h"

#include <stdlib.h>
#include <string.h>

#include "cybsp.h"
#include "ibuki.h"
#include "ai.h"
#include "lines.h"

#define RX_LINE_MAX       (16U)
#define BTN_DEBOUNCE_MS   (30U)
#define TEST_STALL_MAX_MS (2000U)
#define LED_BLINK_HALF_MS (250U) /* alert LED: 2 blinks per second */

static char rx_line[RX_LINE_MAX];
static uint32_t rx_len;
static bool restart_requested;
static bool btn_stable_pressed;
static bool btn_last_raw;
static uint32_t btn_change_ms;

static bool alert_on;          /* PC sent L,1: the RGB LED blinks with red, green and blue on */
static uint32_t blink_last_ms; /* together. The board's power and KitProg lights are red and */
static bool blink_lit;         /* always on, so plain red was easy to miss among them; blue alone
                                * didn't show on our board, so every colour that works lights */

static void led_alert(bool on)
{
    uint32_t v = on ? CYBSP_LED_STATE_ON : CYBSP_LED_STATE_OFF;
    Cy_GPIO_Write(CYBSP_LED_RGB_RED_PORT, CYBSP_LED_RGB_RED_PIN, v);
    Cy_GPIO_Write(CYBSP_LED_RGB_GREEN_PORT, CYBSP_LED_RGB_GREEN_PIN, v);
    Cy_GPIO_Write(CYBSP_LED_RGB_BLUE_PORT, CYBSP_LED_RGB_BLUE_PIN, v);
}

static void set_alert(bool on)
{
    alert_on = on;
    blink_lit = on;
    blink_last_ms = ibuki_millis();
    led_alert(on);
}

static void poll_blink(uint32_t now)
{
    if (alert_on && (now - blink_last_ms) >= LED_BLINK_HALF_MS)
    {
        blink_last_ms = now;
        blink_lit = !blink_lit;
        led_alert(blink_lit);
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
    else if (strcmp(rx_line, "M,1") == 0)
    {
        ibuki_set_antennas(1U);
    }
    else if (strcmp(rx_line, "M,3") == 0)
    {
        ibuki_set_antennas(3U);
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
            set_alert(false); /* a parent is at the board: the alarm is seen */
            ai_acknowledge();
            char* p = line_start();
            *p++ = 'B';
            *p++ = ',';
            *p++ = '1';
            line_send(p);
        }
    }
}

void board_io_init(void)
{
    rx_len = 0U;
    restart_requested = false;
    btn_last_raw = button_raw_pressed();
    btn_stable_pressed = btn_last_raw;
    btn_change_ms = 0U;
    set_alert(false);
}

void board_io_poll(uint32_t now_ms)
{
    poll_uart_rx();
    poll_button(now_ms);
    poll_blink(now_ms);
}

void board_io_alarm(void)
{
    if (!alert_on)
    {
        set_alert(true);
    }
}

bool board_io_take_restart_request(void)
{
    bool requested = restart_requested;
    restart_requested = false;
    return requested;
}
