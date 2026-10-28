/* IBUKI firmware: what main.c calls. Start-up, one radar frame at a time, and the main-loop
 * poll. The work itself is in:
 *   range.c     range FFT of every chirp, summed into the bins of one D line
 *   lines.c     the protocol lines to the PC, through a queue so nothing waits for the UART
 *   board_io.c  alert LED, user button and the commands from the PC
 *   load.c      how busy the CPU is, reported once a minute
 * The line protocol (C, D, S, B, L, R, T) is described in firmware/README.md.
 */
#include "ibuki.h"

#include <math.h>
#include <stdio.h>

#include "cybsp.h"
#include "ai.h"
#include "board_io.h"
#include "lines.h"
#include "load.h"
#include "range.h"
#include "sensors.h"
#include "ibuki_mailbox.h"

static uint32_t d_seq;
static uint32_t last_config_ms;

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

/* status lines: S,t_ms,text */
void ibuki_status(const char* text)
{
    char* p = line_start();
    *p++ = 'S';
    *p++ = ',';
    p = line_put_u32(p, ibuki_millis());
    *p++ = ',';
    line_send(line_put_text(p, text));
}

void ibuki_status_code(const char* text, uint32_t code)
{
    char buf[96];
    snprintf(buf, sizeof(buf), "%s 0x%08lx", text, (unsigned long)code);
    ibuki_status(buf);
}

void ibuki_flush(void)
{
    line_flush();
}

/* the CM33's status from the shared mailbox, sent on as "S,t_ms,<text>" when it changes */
static void relay_cm33_status(void)
{
    static uint32_t seen;
    ibuki_mailbox_refresh();
    if ((IBUKI_MAILBOX->magic == IBUKI_MAILBOX_MAGIC) && (IBUKI_MAILBOX->status_seq != seen))
    {
        char text[sizeof(IBUKI_MAILBOX->status)];
        seen = IBUKI_MAILBOX->status_seq;
        memcpy(text, (const void*)IBUKI_MAILBOX->status, sizeof(text));
        text[sizeof(text) - 1U] = '\0';
        ibuki_status(text);
    }
}

/* C,first_cm,step_mm,n,scale */
static void send_config(void)
{
    char* p = line_start();
    *p++ = 'C';
    *p++ = ',';
    p = line_put_tenths(p, (IBUKI_FIRST_BIN * IBUKI_BIN_STEP_UM) / 1000U); /* cm in tenths */
    *p++ = ',';
    p = line_put_tenths(p, IBUKI_BIN_STEP_UM / 100U);                       /* mm in tenths */
    *p++ = ',';
    p = line_put_u32(p, IBUKI_NUM_BINS);
    *p++ = ',';
    p = line_put_u32(p, (uint32_t)IBUKI_SCALE);
    line_send(p);
}

/* E,seq,t_ms,<base64>: all 3 antennas (after "M,3"). The bytes are int16 little-endian,
 * antenna after antenna, bin after bin, Re then Im: 3 x 24 x 2 values, averaged like the D
 * line but not multiplied by IBUKI_SCALE, so they fit 16 bits (clamped if ever larger).
 * Text at 115200 baud can't carry 3 antennas as decimal numbers; base64 can. */
static void send_all_antennas(void)
{
    static uint8_t raw[IBUKI_RX_ANTENNAS * 2U * IBUKI_NUM_BINS * 2U];
    uint32_t t0 = load_cycles();
    const float k = 1.0f / (float)(range_frames() * IBUKI_CHIRPS_PER_FRAME);
    uint32_t n = 0U;
    for (uint32_t a = 0U; a < IBUKI_RX_ANTENNAS; a++)
    {
        const float* sums = range_sums(a);
        for (uint32_t i = 0U; i < 2U * IBUKI_NUM_BINS; i++)
        {
            long v = lrintf(sums[i] * k);
            v = (v > 32767L) ? 32767L : ((v < -32768L) ? -32768L : v);
            raw[n++] = (uint8_t)((uint16_t)v & 0xFFU);
            raw[n++] = (uint8_t)((uint16_t)v >> 8);
        }
    }
    char* p = line_start();
    *p++ = 'E';
    *p++ = ',';
    p = line_put_u32(p, d_seq++);
    *p++ = ',';
    p = line_put_u32(p, ibuki_millis());
    *p++ = ',';
    p = line_put_base64(p, raw, n);
    load_add(LOAD_OUT, t0);
    line_send(p);
}

/* D,seq,t_ms,I0,Q0,...,In-1,Qn-1: the bin sums averaged over all chirps of the summed frames */
static void send_data(void)
{
    if (range_antennas() != 1U)
    {
        send_all_antennas();
        return;
    }
    uint32_t t0 = load_cycles();
    const float* sums = range_sums(0U);
    const float k = (float)IBUKI_SCALE / (float)(range_frames() * IBUKI_CHIRPS_PER_FRAME);
    char* p = line_start();
    *p++ = 'D';
    *p++ = ',';
    p = line_put_u32(p, d_seq++);
    *p++ = ',';
    p = line_put_u32(p, ibuki_millis());
    for (uint32_t b = 0U; b < IBUKI_NUM_BINS; b++)
    {
        *p++ = ',';
        p = line_put_i32(p, (int32_t)lrintf(sums[2U * b] * k));
        *p++ = ',';
        p = line_put_i32(p, (int32_t)lrintf(sums[(2U * b) + 1U] * k));
    }
    load_add(LOAD_OUT, t0); /* making the line; line_send counts the sending */
    line_send(p);

    /* the same values, unscaled, for the models on the board */
    float z[2U * IBUKI_NUM_BINS];
    const float u = 1.0f / (float)(range_frames() * IBUKI_CHIRPS_PER_FRAME);
    for (uint32_t i = 0U; i < 2U * IBUKI_NUM_BINS; i++)
    {
        z[i] = sums[i] * u;
    }
    ai_add_line(z);
}

void ibuki_init(void)
{
    SysTick_Config(SystemCoreClock / 1000U);

    if (!range_init())
    {
        ibuki_status("rfft init failed");
    }
    d_seq = 0U;
    board_io_init();

    ibuki_status("ibuki fw start");
    send_config();
    last_config_ms = ibuki_millis();
    load_init();
    ai_init();
    sensors_init();
}

void ibuki_on_frame(const float* frame)
{
    uint32_t t0 = load_cycles();
    range_add_frame(frame);
    load_add(LOAD_DSP, t0);

    if (range_frames() >= IBUKI_FRAMES_PER_LINE)
    {
        send_data();
        range_clear();
    }
}

void ibuki_reset_frames(void)
{
    range_clear();
}

void ibuki_poll(void)
{
    uint32_t now = ibuki_millis();
    if ((now - last_config_ms) >= IBUKI_CONFIG_PERIOD_MS)
    {
        send_config();
        last_config_ms = now;
    }
    load_poll(now);
    relay_cm33_status();
    sensors_poll(now);
    line_pump();
    board_io_poll(now);
}

void ibuki_set_antennas(uint32_t n)
{
    range_set_antennas(n);
    ibuki_status(range_antennas() == 1U ? "antennas 1 (D lines)" : "antennas 3 (E lines)");
}

bool ibuki_take_restart_request(void)
{
    return board_io_take_restart_request();
}
