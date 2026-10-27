/* Range FFT. For each chirp of one RX antenna: subtract the chirp mean (DC), apply a Hann
 * window and take a 64-point real FFT. The complex bins of the range gate (IBUKI_NUM_BINS bins
 * from IBUKI_FIRST_BIN, see ibuki.h) are summed over the chirps of every frame added since the
 * last range_clear(), for one antenna (IBUKI_RX_USED) or all three. ibuki.c turns the sums into one D line every IBUKI_FRAMES_PER_LINE frames. */
#ifndef IBUKI_RANGE_H
#define IBUKI_RANGE_H

#include <stdbool.h>
#include <stdint.h>

bool         range_init(void);                    /* window and FFT tables; false if the FFT failed */
void         range_add_frame(const float* frame); /* one radar frame (layout in ibuki.h) */
uint32_t     range_frames(void);                  /* frames summed since the last clear */
const float* range_sums(uint32_t antenna);        /* Re, Im of each bin: 2 * IBUKI_NUM_BINS values;
                                                     antenna 0 in 1-antenna mode, 0..2 with all 3 */
void         range_set_antennas(uint32_t n);      /* 1 (IBUKI_RX_USED, the default) or 3 (all) */
uint32_t     range_antennas(void);
void         range_clear(void);

#endif /* IBUKI_RANGE_H */
