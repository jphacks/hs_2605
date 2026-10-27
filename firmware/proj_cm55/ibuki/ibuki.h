/* IBUKI firmware: what main.c calls, and the radar settings shared by the modules in this
 * folder (range.c, lines.c, board_io.c, load.c). */
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

/* Range gate: FFT bins 4..27 = 15.0 cm .. 101.25 cm, 3.75 cm per bin (4 GHz sweep).
 * A child in a bed can be close to the board, so the near bins are kept. Bins under ~28 cm also
 * hold the radar's own TX->RX leakage (measured on the board); the PC removes the static part,
 * and a close child reflects much more strongly than one at 50 cm. */
#define IBUKI_FIRST_BIN         (4U)
#define IBUKI_NUM_BINS          (24U)
#define IBUKI_BIN_STEP_UM       (37500U) /* bin step in micrometres */

#define IBUKI_FRAMES_PER_LINE   (2U)    /* 2 frames averaged per D line: ~16.6 lines/s */
#define IBUKI_SCALE             (10)    /* D line integer = value * IBUKI_SCALE */
#define IBUKI_CONFIG_PERIOD_MS  (5000U) /* C line period */
#define IBUKI_LOAD_PERIOD_MS    (60000U) /* "S,t_ms,load ..." period: how busy the CPU was */

void     ibuki_init(void);                 /* call after the debug UART is up */
void     ibuki_on_frame(const float* frame); /* one full radar frame of raw samples */
void     ibuki_poll(void);                 /* call often from the main loop */
void     ibuki_status(const char* text);   /* sends "S,t_ms,text" */
void     ibuki_status_code(const char* text, uint32_t code); /* "S,t_ms,text 0x........" */
void     ibuki_set_antennas(uint32_t n); /* 1: D lines (default), 3: E lines with all antennas */
void     ibuki_flush(void);                /* wait until all queued output is sent */
uint32_t ibuki_millis(void);
void     ibuki_reset_frames(void);         /* drop a half-filled frame average (radar restart) */
bool     ibuki_take_restart_request(void); /* true once after the PC sent "R,1" */

#endif /* IBUKI_H */
