/* Protocol lines to the PC, through an output queue (see lines.h).
 *
 * Why a queue: at 115200 baud a D line takes ~26 ms on the wire and one goes out every 60 ms.
 * Written with a blocking call (fputs), making and sending lines took 15.7% of the CPU, most of
 * it waiting for the UART (measured with load.c). Now lines go into this queue and line_pump()
 * moves bytes into the UART's hardware FIFO whenever there is room: 0.0%, and no lines lost.
 */
#include "lines.h"

#include <string.h>

#include "cybsp.h"
#include "load.h"

#define QUEUE_SIZE (8192U) /* bytes waiting for the UART, ~0.7 s of output; a power of 2 */

static char line[LINES_BUF_SIZE];

/* The counters only ever grow (and wrap at 2^32, which QUEUE_SIZE divides):
 * bytes waiting = head - tail. */
static uint8_t queue[QUEUE_SIZE];
static uint32_t head;    /* bytes ever queued */
static uint32_t tail;    /* bytes ever handed to the UART */
static uint32_t peak;    /* most bytes waiting since the last line_take_peak() */
static uint32_t dropped; /* lines dropped because the queue was full: the PC sees a seq gap */

char* line_start(void)
{
    return line;
}

char* line_put_u32(char* p, uint32_t v)
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

char* line_put_i32(char* p, int32_t v)
{
    if (v < 0)
    {
        *p++ = '-';
        return line_put_u32(p, (uint32_t)(-(int64_t)v));
    }
    return line_put_u32(p, (uint32_t)v);
}

char* line_put_tenths(char* p, uint32_t tenths)
{
    p = line_put_u32(p, tenths / 10U);
    *p++ = '.';
    *p++ = (char)('0' + (tenths % 10U));
    return p;
}

char* line_put_text(char* p, const char* text)
{
    size_t room = LINES_BUF_SIZE - 2U - (size_t)(p - line); /* 2: the "\r\n" line_send adds */
    size_t n = strlen(text);
    if (n > room)
    {
        n = room;
    }
    memcpy(p, text, n);
    return p + n;
}

/* Hand queued bytes to the UART's hardware FIFO, as many as fit right now. Never waits. */
void line_pump(void)
{
    while (tail != head)
    {
        uint32_t start = tail % QUEUE_SIZE;
        uint32_t n = head - tail;
        if (n > (QUEUE_SIZE - start))
        {
            n = QUEUE_SIZE - start; /* up to the end of the buffer; the rest on the next turn */
        }
        uint32_t put = Cy_SCB_UART_PutArray(CYBSP_DEBUG_UART_HW, &queue[start], n);
        tail += put;
        if (put < n)
        {
            break; /* the FIFO is full */
        }
    }
}

/* A full queue drops the whole line rather than waiting: radar frames must keep being read,
 * and the PC notices the missing seq number. */
void line_send(char* end)
{
    uint32_t t0 = load_cycles();
    *end++ = '\r'; /* the same "\r\n" ending as before, when retarget-io added the '\r' */
    *end++ = '\n';
    uint32_t len = (uint32_t)(end - line);
    uint32_t waiting = head - tail;
    if ((QUEUE_SIZE - waiting) < len)
    {
        dropped++;
    }
    else
    {
        for (uint32_t i = 0U; i < len; i++)
        {
            queue[(head + i) % QUEUE_SIZE] = (uint8_t)line[i];
        }
        head += len;
        if ((waiting + len) > peak)
        {
            peak = waiting + len;
        }
        line_pump();
    }
    load_add(LOAD_OUT, t0);
}

void line_flush(void)
{
    while (tail != head)
    {
        line_pump();
    }
}

uint32_t line_take_peak(void)
{
    uint32_t p = peak;
    peak = head - tail;
    return p;
}

uint32_t line_dropped(void)
{
    return dropped;
}
