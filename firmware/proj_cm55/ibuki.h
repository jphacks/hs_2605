/* IBUKI firmware: per-chirp range FFT and the C/D/S/B/L line protocol over the debug UART. */
#ifndef IBUKI_H
#define IBUKI_H

#include <stdint.h>
#include <stdbool.h>

/* Frame layout for radar preset P1: chirp after chirp, each chirp sample after sample,
 * and the 3 RX antennas interleaved inside each sample. */
#define IBUKI_SAMPLES_PER_CHIRP (64U)
#define IBUKI_CHIRPS_PER_FRAME  (32U)
#define IBUKI_RX_ANTENNAS       (3U)
#define IBUKI_RX_USED           (0U)    /* first RX antenna only, for now */

/* Range gate: FFT bins 8..31 = 30.0 cm .. 116.25 cm, 3.75 cm per bin (4 GHz sweep). */
#define IBUKI_FIRST_BIN         (8U)
#define IBUKI_NUM_BINS          (24U)
#define IBUKI_BIN_STEP_UM       (37500U) /* bin step in micrometres */

#define IBUKI_FRAMES_PER_LINE   (2U)    /* 2 frames averaged per D line: ~16.6 lines/s */
#define IBUKI_SCALE             (10)    /* D line integer = value * IBUKI_SCALE */
#define IBUKI_CONFIG_PERIOD_MS  (5000U) /* C line period */

void     ibuki_init(void);                 /* call after the debug UART is up */
void     ibuki_on_frame(const float* frame); /* one full radar frame of raw samples */
void     ibuki_poll(void);                 /* call often from the main loop */
void     ibuki_status(const char* text);   /* sends "S,t_ms,text" */
void     ibuki_status_code(const char* text, uint32_t code); /* "S,t_ms,text 0x........" */
uint32_t ibuki_millis(void);

#endif /* IBUKI_H */
