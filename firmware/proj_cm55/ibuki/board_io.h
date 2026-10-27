/* The board's alert LED and user button, and the short commands the PC sends:
 *   L,1 / L,0   alert LED blinking twice a second (red, green and blue together) / off
 *   M,1 / M,3   send one antenna as D lines (the default) / all 3 as E lines
 *   R,1         restart the radar (main.c asks with ibuki_take_restart_request())
 *   T,<ms>      test only: stall the main loop for up to 2 s, so the radar FIFO overflows
 * A press of the user button sends "B,1". */
#ifndef IBUKI_BOARD_IO_H
#define IBUKI_BOARD_IO_H

#include <stdbool.h>
#include <stdint.h>

void board_io_init(void);
void board_io_poll(uint32_t now_ms);          /* commands from the PC, the button, the blinking */
bool board_io_take_restart_request(void);
void board_io_alarm(void);                    /* the board's own alarm (ai.c): blink, until SW1 */     /* true once after the PC sent "R,1" */

#endif /* IBUKI_BOARD_IO_H */
