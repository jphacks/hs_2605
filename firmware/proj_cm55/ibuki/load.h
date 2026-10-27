/* CPU load: core clock cycles spent on each part (from the DWT cycle counter), reported every
 * IBUKI_LOAD_PERIOD_MS as "S,t_ms,load dsp 1.5% out 0.0% sleep 95.3% 7154/s queue 249 dropped 0":
 *   dsp      the range FFTs (range.c)
 *   out      making and queueing lines (lines.c)
 *   sleep    time inside the sleep call (main.c sleeps until the next interrupt when it is
 *            only waiting), and how many times per second it went to sleep. The cycle counter
 *            keeps counting while the core sleeps (measured), so this is the sleep time itself
 *   queue    the most bytes that waited in the output queue since the last report
 *   dropped  lines lost to a full queue since start
 * The rest of the time the main loop only polls. */
#ifndef IBUKI_LOAD_H
#define IBUKI_LOAD_H

#include <stdint.h>

typedef enum
{
    LOAD_DSP,
    LOAD_OUT,
    LOAD_SLEEP /* inside the sleep call (main.c) */
} load_part_t;

void     load_init(void);
uint32_t load_cycles(void);                          /* the cycle counter now, for load_add() */
void     load_add(load_part_t part, uint32_t since); /* count the cycles from `since` to now */
void     load_poll(uint32_t now_ms);                 /* sends the load line when it is due */

#endif /* IBUKI_LOAD_H */
