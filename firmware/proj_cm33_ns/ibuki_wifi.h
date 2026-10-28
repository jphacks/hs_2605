/* The board's own phone alerts over Wi-Fi, on the CM33 core (FreeRTOS).
 *
 * Joins eduroam (PEAP with MSCHAPv2, Infineon's enterprise-security library), then waits for
 * board alarms from the M55 (shared mailbox, shared_src/ibuki_mailbox.h) and sends each one to
 * ntfy over HTTPS, the same alert the laptop app sends. Its progress goes back through the
 * mailbox and comes out on the PC as S lines ("wifi: ...").
 *
 * Login and ntfy topic come from firmware/wifi_secrets.h (not in git). Without that file the
 * task only reports "wifi: off". */
#ifndef IBUKI_WIFI_H
#define IBUKI_WIFI_H

void ibuki_wifi_task(void* arg);

#endif /* IBUKI_WIFI_H */
