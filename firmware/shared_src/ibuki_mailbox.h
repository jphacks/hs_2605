/* A small mailbox between the two cores, in the SOC memory both of them map at the same
 * address (m33_m55_shared, 0x262FC000; on the M55 it is reserved and otherwise unused).
 *
 *   M55 (radar, AI)  -> CM33 (Wi-Fi):  every board alarm (no_breath / left_bed)
 *   CM33 -> M55:                       a short Wi-Fi status text, which the M55 sends to the PC as
 *                                      an S line (the CM33 has no serial port of its own here)
 *
 * The CM33 starts first and clears the mailbox before it starts the M55. Each side only writes
 * its own fields; a counter that goes up tells the other side there is something new. The M55
 * has a data cache, so it cleans/invalidates the mailbox around every access. */
#ifndef IBUKI_MAILBOX_H
#define IBUKI_MAILBOX_H

#include <stdint.h>
#include <string.h>

#include "cybsp.h" /* the device and this core's memory map (CYMEM_...) */

#define IBUKI_MAILBOX_MAGIC  (0x49425531UL) /* "IBU1" */
#define IBUKI_ALARM_NO_BREATH (1UL)
#define IBUKI_ALARM_LEFT_BED  (2UL)

typedef struct
{
    uint32_t magic;          /* IBUKI_MAILBOX_MAGIC once the CM33 has cleared it */
    uint32_t alarm_seq;      /* M55: +1 per alarm */
    uint32_t alarm_kind;     /* M55: IBUKI_ALARM_... */
    uint32_t alarm_since_s;  /* M55: seconds without breathing (no_breath) */
    uint32_t status_seq;     /* CM33: +1 per new status */
    char status[56];         /* CM33: e.g. "wifi: connected to eduroam" */
} ibuki_mailbox_t;

#define IBUKI_MAILBOX ((volatile ibuki_mailbox_t*)CYMEM_m33_m55_shared_START)

/* the M55's data cache: write our changes out / read the other core's changes in */
static inline void ibuki_mailbox_flush(void)
{
#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)
    SCB_CleanDCache_by_Addr((void*)IBUKI_MAILBOX, (int32_t)sizeof(ibuki_mailbox_t));
#endif
}

static inline void ibuki_mailbox_refresh(void)
{
#if defined(__DCACHE_PRESENT) && (__DCACHE_PRESENT == 1U)
    SCB_InvalidateDCache_by_Addr((void*)IBUKI_MAILBOX, (int32_t)sizeof(ibuki_mailbox_t));
#endif
}

#endif /* IBUKI_MAILBOX_H */
