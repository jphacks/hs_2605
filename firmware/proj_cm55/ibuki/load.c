/* CPU load (see load.h). */
#include "load.h"

#include "cybsp.h"
#include "ibuki.h"
#include "lines.h"

/* 64 bits: a minute at 400 MHz is 24e9 cycles, more than 32 bits hold */
static uint64_t cycles_dsp;
static uint64_t cycles_out;
static uint64_t cycles_sleep;
static uint32_t sleeps;       /* times the sleep call was made */
static uint32_t start_ms;

void load_init(void)
{
    DCB->DEMCR |= DCB_DEMCR_TRCENA_Msk; /* enable the debug blocks, then the cycle counter */
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;
    cycles_dsp = 0U;
    cycles_out = 0U;
    cycles_sleep = 0U;
    sleeps = 0U;
    start_ms = ibuki_millis();
}

uint32_t load_cycles(void)
{
    return DWT->CYCCNT;
}

void load_add(load_part_t part, uint32_t since)
{
    uint32_t n = DWT->CYCCNT - since; /* right across a counter wrap too */
    if (part == LOAD_DSP)
    {
        cycles_dsp += n;
    }
    else if (part == LOAD_OUT)
    {
        cycles_out += n;
    }
    else
    {
        cycles_sleep += n;
        sleeps++;
    }
}

/* Cycles used / cycles available since the last report. The available cycles come from the
 * millisecond clock, as the cycle counter wraps every ~10 s. */
void load_poll(uint32_t now_ms)
{
    if ((now_ms - start_ms) < IBUKI_LOAD_PERIOD_MS)
    {
        return;
    }
    uint64_t avail = (uint64_t)(now_ms - start_ms) * (SystemCoreClock / 1000U);
    uint32_t dsp = (uint32_t)((cycles_dsp * 1000U) / avail); /* tenths of a percent */
    uint32_t out = (uint32_t)((cycles_out * 1000U) / avail);
    uint32_t asleep = (uint32_t)((cycles_sleep * 1000U) / avail);
    uint32_t per_s = (uint32_t)(((uint64_t)sleeps * 1000U) / (now_ms - start_ms));
    char* p = line_start();
    *p++ = 'S';
    *p++ = ',';
    p = line_put_u32(p, now_ms);
    p = line_put_text(p, ",load dsp ");
    p = line_put_tenths(p, dsp);
    p = line_put_text(p, "% out ");
    p = line_put_tenths(p, out);
    p = line_put_text(p, "% sleep ");
    p = line_put_tenths(p, asleep);
    p = line_put_text(p, "% ");
    p = line_put_u32(p, per_s);
    p = line_put_text(p, "/s queue ");
    p = line_put_u32(p, line_take_peak());
    p = line_put_text(p, " dropped ");
    p = line_put_u32(p, line_dropped());
    line_send(p);
    cycles_dsp = 0U;
    cycles_out = 0U;
    cycles_sleep = 0U;
    sleeps = 0U;
    start_ms = now_ms;
}
