/* Copy to firmware/wifi_secrets.h (git-ignored, never committed) and fill in. The board then joins
 * this Wi-Fi by itself and sends its own alarms to the phone (firmware/proj_cm33_ns/ibuki_wifi.c).
 * Without the file the firmware builds as before and the board reports "wifi: off". */
#ifndef WIFI_SECRETS_H
#define WIFI_SECRETS_H

/* eduroam (WPA2-Enterprise, PEAP with MSCHAPv2) */
#define IBUKI_WIFI_SSID          "eduroam"
#define IBUKI_WIFI_EAP_IDENTITY  "your-id@your-university.ac.jp"
#define IBUKI_WIFI_EAP_PASSWORD  "your-password"
#define IBUKI_WIFI_EAP_ANON_ID   ""   /* an anonymous outer identity, if your university gives one */

/* ntfy, the same topic and token as the laptop app's .env */
#define IBUKI_NTFY_TOPIC "your-topic"
#define IBUKI_NTFY_TOKEN ""

#endif /* WIFI_SECRETS_H */
