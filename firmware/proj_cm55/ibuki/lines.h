/* Protocol lines (C, D, S, B) to the PC over the debug UART, through a queue, so making a line
 * never waits for the UART. One line is built at a time, in a shared buffer:
 *
 *     char* p = line_start();
 *     *p++ = 'S';
 *     *p++ = ',';
 *     p = line_put_u32(p, ibuki_millis());
 *     line_send(p);
 *
 * line_pump() must be called often (the main loop does, through ibuki_poll()). */
#ifndef IBUKI_LINES_H
#define IBUKI_LINES_H

#include <stdint.h>

#define LINES_BUF_SIZE (512U) /* the longest line, a D line, is about 410 bytes */

char*    line_start(void);                          /* the shared buffer, to build one line in */
char*    line_put_u32(char* p, uint32_t v);         /* decimal digits */
char*    line_put_i32(char* p, int32_t v);
char*    line_put_tenths(char* p, uint32_t tenths); /* one decimal, from tenths: 375 -> "37.5" */
char*    line_put_text(char* p, const char* text);  /* cut short if the line would not fit */
void     line_send(char* end);                      /* queue the line that ends at `end` */
void     line_pump(void);                           /* move queued bytes into the UART FIFO */
void     line_flush(void);                          /* wait until everything queued is sent */
uint32_t line_take_peak(void);                      /* most bytes waiting since the last call */
uint32_t line_dropped(void);                        /* lines dropped (queue full) since start */

#endif /* IBUKI_LINES_H */
