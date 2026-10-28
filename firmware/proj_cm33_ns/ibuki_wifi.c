/* The board's own phone alerts over Wi-Fi (see ibuki_wifi.h).
 *
 * The Wi-Fi chip setup (SDIO, its pins and interrupts) follows Infineon's
 * mtb-example-psoc-edge-wifi-https-client, the eduroam join follows Infineon's
 * mtb-tester-psoc-edge-enterprise-security (ent_sec_utility.c), and the HTTPS request follows
 * the HTTPS client example again. */
#include "ibuki_wifi.h"

#include <stdio.h>
#include <time.h>
#include <string.h>

#include "cybsp.h"
#include "FreeRTOS.h"
#include "task.h"
#include "cy_wcm.h"
#include "cy_enterprise_security.h"
#include "cy_http_client_api.h"
#include "cy_log.h"
#include "mtb_hal.h"

#include "ibuki_certs.h"
#include "ibuki_mailbox.h"

#if __has_include("../wifi_secrets.h")
#include "../wifi_secrets.h"
#define WIFI_CONFIGURED 1
#else
#define WIFI_CONFIGURED 0
#endif

#define SDIO_INTERRUPT_PRIORITY      (7U)          /* as Infineon's example */
#define HOST_WAKE_INTERRUPT_PRIORITY (2U)
#define SDIO_FREQUENCY_HZ            (25000000U)
#define SDIO_BLOCK_SIZE              (64U)
#define REJOIN_AFTER_MS              (30000U)      /* try again this long after a failed join */
#define POLL_MS                      (200U)        /* how often the mailbox is checked */
#define HTTP_TIMEOUT_MS              (5000U)
#define NTFY_HOST                    "ntfy.sh"

static mtb_hal_sdio_t sdio_instance;
static cy_stc_sd_host_context_t sdhc_host_context;
static cy_wcm_config_t wcm_config;

#if (CY_CFG_PWR_SYS_IDLE_MODE == CY_CFG_PWR_MODE_DEEPSLEEP)
/* This board idles in deep sleep (FreeRTOS tickless idle). The SD host must be told before and
 * after, or the Wi-Fi bus stops mid-transfer: Infineon's example registers this callback. */
static cy_stc_syspm_callback_params_t sdhc_ds_params = {.context = &sdhc_host_context, .base = CYBSP_WIFI_SDIO_HW};
static cy_stc_syspm_callback_t sdhc_ds_callback = {
    .callback = Cy_SD_Host_DeepSleepCallback,
    .skipMode = 0U,                 /* SYSPM_SKIP_MODE in the example */
    .type = CY_SYSPM_DEEPSLEEP,
    .callbackParams = &sdhc_ds_params,
    .prevItm = NULL,
    .nextItm = NULL,
    .order = 1U                     /* SYSPM_CALLBACK_ORDER in the example */
};
#endif

/* a status for the PC, through the mailbox (the M55 prints it as an S line). It waits (up to
 * 200 ms) until the M55 has printed the previous one, so bursts such as the login log arrive
 * whole; in a fault hook or before FreeRTOS runs it doesn't wait. */
static void status(const char* text)
{
    if (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING)
    {
        for (uint32_t i = 0U; (IBUKI_MAILBOX->status_ack != IBUKI_MAILBOX->status_seq) && (i < 200U); i++)
        {
            vTaskDelay(pdMS_TO_TICKS(1U));
        }
    }
    strncpy((char*)IBUKI_MAILBOX->status, text, sizeof(IBUKI_MAILBOX->status) - 1U);
    IBUKI_MAILBOX->status[sizeof(IBUKI_MAILBOX->status) - 1U] = '\0';
    IBUKI_MAILBOX->status_seq = IBUKI_MAILBOX->status_seq + 1U;
}

static void status_code(const char* text, cy_rslt_t r)
{
    char buf[sizeof(IBUKI_MAILBOX->status)];
    snprintf(buf, sizeof(buf), "%s 0x%08lx", text, (unsigned long)r);
    status(buf);
}

#if WIFI_CONFIGURED
static void sdio_interrupt_handler(void)
{
    mtb_hal_sdio_process_interrupt(&sdio_instance);
}

static void host_wake_interrupt_handler(void)
{
    mtb_hal_gpio_process_interrupt(&wcm_config.wifi_host_wake_pin);
}

/* the Wi-Fi chip's SDIO bus and pins (Infineon's app_sdio_init) */
static bool sdio_init(void)
{
    cy_stc_sysint_t sdio_irq = {.intrSrc = CYBSP_WIFI_SDIO_IRQ, .intrPriority = SDIO_INTERRUPT_PRIORITY};
    cy_stc_sysint_t wake_irq = {.intrSrc = CYBSP_WIFI_HOST_WAKE_IRQ, .intrPriority = HOST_WAKE_INTERRUPT_PRIORITY};
    mtb_hal_sdio_cfg_t cfg;

    if (Cy_SysInt_Init(&sdio_irq, sdio_interrupt_handler) != CY_SYSINT_SUCCESS)
    {
        return false;
    }
    NVIC_EnableIRQ(CYBSP_WIFI_SDIO_IRQ);
    if (mtb_hal_sdio_setup(&sdio_instance, &CYBSP_WIFI_SDIO_sdio_hal_config, NULL, &sdhc_host_context) != CY_RSLT_SUCCESS)
    {
        return false;
    }
    Cy_SD_Host_Enable(CYBSP_WIFI_SDIO_HW);
    Cy_SD_Host_Init(CYBSP_WIFI_SDIO_HW, CYBSP_WIFI_SDIO_sdio_hal_config.host_config, &sdhc_host_context);
    Cy_SD_Host_SetHostBusWidth(CYBSP_WIFI_SDIO_HW, CY_SD_HOST_BUS_WIDTH_4_BIT);
    cfg.frequencyhal_hz = SDIO_FREQUENCY_HZ;
    cfg.block_size = SDIO_BLOCK_SIZE;
    mtb_hal_sdio_configure(&sdio_instance, &cfg);
    mtb_hal_gpio_setup(&wcm_config.wifi_wl_pin, CYBSP_WIFI_WL_REG_ON_PORT_NUM, CYBSP_WIFI_WL_REG_ON_PIN);
    mtb_hal_gpio_setup(&wcm_config.wifi_host_wake_pin, CYBSP_WIFI_HOST_WAKE_PORT_NUM, CYBSP_WIFI_HOST_WAKE_PIN);
    if (Cy_SysInt_Init(&wake_irq, host_wake_interrupt_handler) != CY_SYSINT_SUCCESS)
    {
        return false;
    }
    NVIC_EnableIRQ(CYBSP_WIFI_HOST_WAKE_IRQ);
    return true;
}

/* Before each join: is the network there at all? A failed join alone can't tell "not in range"
 * from "login refused", so the scan answers the first. */
static volatile bool scan_done;
static volatile int16_t best_rssi;
static volatile uint32_t seen_aps;

static void scan_callback(cy_wcm_scan_result_t* result, void* user_data, cy_wcm_scan_status_t scan_status)
{
    (void)user_data;
    if ((scan_status == CY_WCM_SCAN_INCOMPLETE) && (result != NULL))
    {
        seen_aps++;
        if (result->signal_strength > best_rssi)
        {
            best_rssi = result->signal_strength;
        }
    }
    else if (scan_status == CY_WCM_SCAN_COMPLETE)
    {
        scan_done = true;
    }
}

/* true if the network answered the scan; reports what it found */
static bool network_in_range(void)
{
    static cy_wcm_scan_filter_t filter;
    memset(&filter, 0, sizeof(filter));
    filter.mode = CY_WCM_SCAN_FILTER_TYPE_SSID;
    strncpy((char*)filter.param.SSID, IBUKI_WIFI_SSID, sizeof(filter.param.SSID) - 1U);
    scan_done = false;
    best_rssi = -127;
    seen_aps = 0U;
    if (cy_wcm_start_scan(scan_callback, NULL, &filter) != CY_RSLT_SUCCESS)
    {
        return true; /* can't tell: try to join anyway */
    }
    for (uint32_t waited = 0U; !scan_done && (waited < 10000U); waited += 100U)
    {
        vTaskDelay(pdMS_TO_TICKS(100U));
    }
    char buf[sizeof(IBUKI_MAILBOX->status)];
    if (seen_aps == 0U)
    {
        status("wifi: " IBUKI_WIFI_SSID " not in range");
        return false;
    }
    snprintf(buf, sizeof(buf), "wifi: %s found (%lu), best %d dBm", IBUKI_WIFI_SSID, (unsigned long)seen_aps, best_rssi);
    status(buf);
    vTaskDelay(pdMS_TO_TICKS(300U)); /* let the M55 relay it before the next status */
    return true;
}

/* eduroam: PEAPv0 with MSCHAPv2 (ent_sec_utility.c, join_ent) */
static cy_enterprise_security_t ent_handle;
static cy_enterprise_security_parameters_t ent;

static cy_rslt_t join_eduroam(void)
{
    memset(&ent, 0, sizeof(ent));
    strncpy(ent.ssid, IBUKI_WIFI_SSID, sizeof(ent.ssid) - 1U);
    ent.eap_type = CY_ENTERPRISE_SECURITY_EAP_TYPE_PEAP;
    ent.auth_type = CY_ENTERPRISE_SECURITY_AUTH_TYPE_WPA2_AES;
    ent.ca_cert = (char*)IBUKI_EDUROAM_CA_PEM;
    ent.client_cert = NULL; /* PEAP: no client certificate */
    ent.client_key = NULL;
    /* the outer (visible) identity: the anonymous one if set, otherwise the login */
    strncpy(ent.outer_eap_identity, (sizeof(IBUKI_WIFI_EAP_ANON_ID) > 1U) ? IBUKI_WIFI_EAP_ANON_ID : IBUKI_WIFI_EAP_IDENTITY,
            sizeof(ent.outer_eap_identity) - 1U);
#ifdef IBUKI_WIFI_EAP_INNER_ID
    /* some universities want only the user ID inside the tunnel, the realm only outside */
    strncpy(ent.phase2.inner_identity, IBUKI_WIFI_EAP_INNER_ID, sizeof(ent.phase2.inner_identity) - 1U);
#else
    strncpy(ent.phase2.inner_identity, IBUKI_WIFI_EAP_IDENTITY, sizeof(ent.phase2.inner_identity) - 1U);
#endif
    strncpy(ent.phase2.inner_password, IBUKI_WIFI_EAP_PASSWORD, sizeof(ent.phase2.inner_password) - 1U);
    ent.phase2.inner_eap_type = CY_ENTERPRISE_SECURITY_EAP_TYPE_MSCHAPV2;
    ent.phase2.tunnel_auth_type = CY_ENTERPRISE_SECURITY_TUNNEL_TYPE_MSCHAPV2;

    cy_rslt_t r = cy_enterprise_security_create(&ent_handle, &ent);
    if (r == CY_RSLT_SUCCESS)
    {
        r = cy_enterprise_security_join(ent_handle);
        if (r != CY_RSLT_SUCCESS)
        {
            (void)cy_enterprise_security_delete(&ent_handle);
        }
    }
    memset(ent.phase2.inner_password, 0, sizeof(ent.phase2.inner_password)); /* not kept in RAM */
    return r;
}

/* one alarm to ntfy as JSON over HTTPS (secure_http_client.c, send_http_request) */
static cy_http_client_t http;
static uint8_t http_buffer[2048];

static cy_rslt_t send_alert(uint32_t kind, uint32_t since_s)
{
    static cy_awsport_ssl_credentials_t tls;
    static cy_awsport_server_info_t server;
    static bool created;
    char body[384];
    if (kind == IBUKI_ALARM_NO_BREATH)
    {
        snprintf(body, sizeof(body),
                 "{\"topic\":\"%s\",\"title\":\"【緊急】呼吸が確認できません\",\"message\":\"赤ちゃんの呼吸が%lu秒間見えません。"
                 "すぐに様子を見てください。（ボードから直接）\",\"priority\":5,\"tags\":[\"rotating_light\"]}",
                 IBUKI_NTFY_TOPIC, (unsigned long)since_s);
    }
    else
    {
        snprintf(body, sizeof(body),
                 "{\"topic\":\"%s\",\"title\":\"【注意】ベッドにいません\",\"message\":\"赤ちゃんがベッドにいません。"
                 "様子を確認してください。（ボードから直接）\",\"priority\":4,\"tags\":[\"warning\"]}",
                 IBUKI_NTFY_TOPIC);
    }
    if (!created)
    {
        memset(&tls, 0, sizeof(tls));
        memset(&server, 0, sizeof(server));
        tls.root_ca = IBUKI_NTFY_ROOT_CA_PEM;
        tls.root_ca_size = sizeof(IBUKI_NTFY_ROOT_CA_PEM);
        tls.sni_host_name = NTFY_HOST;
        tls.sni_host_name_size = sizeof(NTFY_HOST);
        server.host_name = NTFY_HOST;
        server.port = 443U;
        cy_rslt_t r = cy_http_client_init();
        if (r == CY_RSLT_SUCCESS)
        {
            r = cy_http_client_create(&tls, &server, NULL, NULL, &http);
        }
        if (r != CY_RSLT_SUCCESS)
        {
            return r;
        }
        created = true;
    }
    cy_rslt_t r = cy_http_client_connect(http, HTTP_TIMEOUT_MS, HTTP_TIMEOUT_MS);
    if (r != CY_RSLT_SUCCESS)
    {
        return r;
    }
    cy_http_client_request_header_t request;
    cy_http_client_header_t headers[2];
    cy_http_client_response_t response;
    uint32_t n = 0U;
    memset(&request, 0, sizeof(request));
    request.buffer = http_buffer;
    request.buffer_len = sizeof(http_buffer);
    request.method = CY_HTTP_CLIENT_METHOD_POST;
    request.range_start = 0;
    request.range_end = -1;
    request.resource_path = "/";
    headers[n].field = "Content-Type";
    headers[n].field_len = strlen("Content-Type");
    headers[n].value = "application/json";
    headers[n].value_len = strlen("application/json");
    n++;
    static char auth[96];
    if (sizeof(IBUKI_NTFY_TOKEN) > 1U)
    {
        snprintf(auth, sizeof(auth), "Bearer %s", IBUKI_NTFY_TOKEN);
        headers[n].field = "Authorization";
        headers[n].field_len = strlen("Authorization");
        headers[n].value = auth;
        headers[n].value_len = strlen(auth);
        n++;
    }
    r = cy_http_client_write_header(http, &request, headers, n);
    if (r == CY_RSLT_SUCCESS)
    {
        r = cy_http_client_send(http, &request, (uint8_t*)body, strlen(body), &response);
    }
    if ((r == CY_RSLT_SUCCESS) && (response.status_code != 200))
    {
        r = (cy_rslt_t)response.status_code; /* reported as the HTTP status */
    }
    (void)cy_http_client_disconnect(http);
    return r;
}
#endif /* WIFI_CONFIGURED */

/* reports every 10 s while the Wi-Fi driver is still starting, to tell a slow start from a
 * stuck one (and that FreeRTOS on the CM33 keeps running) */
static volatile bool driver_ready;

static void start_watch_task(void* arg)
{
    (void)arg;
    for (uint32_t s = 10U; !driver_ready; s += 10U)
    {
        vTaskDelay(pdMS_TO_TICKS(10000U));
        if (!driver_ready)
        {
            char buf[48];
            snprintf(buf, sizeof(buf), "wifi: driver still starting (%lu s)", (unsigned long)s);
            status(buf);
        }
    }
    vTaskDelete(NULL);
}

/* the enterprise-security library's log (cy_log), line by line into the mailbox */
static int log_to_mailbox(CY_LOG_FACILITY_T facility, CY_LOG_LEVEL_T level, char* msg)
{
    (void)facility;
    (void)level;
    char buf[sizeof(IBUKI_MAILBOX->status)];
    /* drop the log's own "0036 00:00:15.325 " counter and time; keep the message */
    if ((strlen(msg) > 18U) && (msg[4] == ' ') && (msg[7] == ':') && (msg[10] == ':'))
    {
        msg += 18;
    }
    /* one message can hold several lines (" failed\n  ! mbedtls_ssl_handshake returned -0x2700"):
     * join them with " | " so the error code isn't lost */
    char joined[sizeof(IBUKI_MAILBOX->status)];
    size_t n = 0U;
    for (const char* c = msg; (*c != '\0') && (n < (sizeof(joined) - 4U)); c++)
    {
        if ((*c == '\r') || (*c == '\n'))
        {
            if ((n > 0U) && (c[1] != '\0') && (c[1] != '\r') && (c[1] != '\n'))
            {
                joined[n++] = ' ';
                joined[n++] = '|';
                joined[n++] = ' ';
            }
        }
        else
        {
            joined[n++] = *c;
        }
    }
    joined[n] = '\0';
    if (n == 0U)
    {
        return 0;
    }
    snprintf(buf, sizeof(buf), "es: %s", joined);
    status(buf);
    return 0;
}

void ibuki_wifi_task(void* arg)
{
    (void)arg;
    (void)cy_log_init(CY_LOG_INFO, log_to_mailbox, NULL);
#if !WIFI_CONFIGURED
    status("wifi: off (no firmware/wifi_secrets.h)");
    vTaskDelete(NULL);
#else
    /* let the M55 finish its own start-up first: it runs the same board set-up */
    vTaskDelay(pdMS_TO_TICKS(3000U));
    {
        /* the clock TLS uses to check certificate dates (time() -> the RTC, set in main.c) */
        time_t now = time(NULL);
        struct tm tm_now;
        char buf[48];
        (void)gmtime_r(&now, &tm_now);
        strftime(buf, sizeof(buf), "cm33: clock %Y-%m-%d %H:%M:%S", &tm_now);
        status(buf);
        vTaskDelay(pdMS_TO_TICKS(300U));
    }
    status("wifi: starting");
    (void)xTaskCreate(start_watch_task, "wifi watch", 512U, NULL, 1U, NULL);
#if (CY_CFG_PWR_SYS_IDLE_MODE == CY_CFG_PWR_MODE_DEEPSLEEP)
    Cy_SysPm_RegisterCallback(&sdhc_ds_callback);
#endif
    if (!sdio_init())
    {
        status("wifi: chip setup failed");
        vTaskDelete(NULL);
    }
    status("wifi: chip bus ready, starting the Wi-Fi driver");
    wcm_config.interface = CY_WCM_INTERFACE_TYPE_STA;
    wcm_config.wifi_interface_instance = &sdio_instance;
    cy_rslt_t r = cy_wcm_init(&wcm_config);
    if (r != CY_RSLT_SUCCESS)
    {
        status_code("wifi: init failed", r);
        vTaskDelete(NULL);
    }
    driver_ready = true;
    status("wifi: driver ready");
    uint32_t seen = IBUKI_MAILBOX->alarm_seq;
    TickType_t next_join = 0;
    for (;;)
    {
        if (!cy_wcm_is_connected_to_ap() && (xTaskGetTickCount() >= next_join))
        {
            if (!network_in_range())
            {
                next_join = xTaskGetTickCount() + pdMS_TO_TICKS(REJOIN_AFTER_MS);
                vTaskDelay(pdMS_TO_TICKS(POLL_MS));
                continue;
            }
            status("wifi: joining " IBUKI_WIFI_SSID);
            r = join_eduroam();
            if (r == CY_RSLT_SUCCESS)
            {
                status("wifi: connected to " IBUKI_WIFI_SSID);
            }
            else
            {
                status_code("wifi: join failed", r);
                next_join = xTaskGetTickCount() + pdMS_TO_TICKS(REJOIN_AFTER_MS);
            }
        }
        uint32_t seq = IBUKI_MAILBOX->alarm_seq;
        if (seq != seen)
        {
            seen = seq;
            if (cy_wcm_is_connected_to_ap())
            {
                r = send_alert(IBUKI_MAILBOX->alarm_kind, IBUKI_MAILBOX->alarm_since_s);
                if (r == CY_RSLT_SUCCESS)
                {
                    status("wifi: alert sent to the phone");
                }
                else
                {
                    status_code("wifi: alert not sent", r);
                }
            }
            else
            {
                status("wifi: alert not sent (no Wi-Fi)");
            }
        }
        vTaskDelay(pdMS_TO_TICKS(POLL_MS));
    }
#endif
}

/* The CM33 has no serial port here, so when it stops, it leaves the reason in the mailbox for
 * the M55 to print ("cm33: ..."), then halts as FreeRTOS would. */
static void halt_with(const char* why)
{
    status(why);
    __disable_irq();
    for (;;)
    {
    }
}

void vApplicationMallocFailedHook(void)
{
    halt_with("cm33: out of FreeRTOS heap");
}

void vApplicationStackOverflowHook(TaskHandle_t task, char* name)
{
    (void)task;
    char buf[48];
    snprintf(buf, sizeof(buf), "cm33: stack overflow in %s", name);
    halt_with(buf);
}

void HardFault_Handler(void)
{
    halt_with("cm33: hard fault");
}

/* printf on the CM33. Libraries here print (the Wi-Fi driver reports errors with printf), but
 * the debug UART belongs to the M55 and its line protocol, so nothing may write to it from this
 * core. Without a _write, printf hung inside the C library (2026-10-28, found with the
 * debugger: whd_bus_sdio_init -> printf -> fflush). Each printed line now becomes the CM33's
 * status in the mailbox ("cm33: <text>"), so the PC still sees it as an S line. */
int _write(int fd, const char* buf, int len)
{
    static char line[sizeof(IBUKI_MAILBOX->status)];
    static uint32_t n;
    (void)fd;
    for (int i = 0; i < len; i++)
    {
        char c = buf[i];
        if ((c == '\n') || (c == '\r'))
        {
            if (n > 0U)
            {
                line[n] = '\0';
                char out[sizeof(IBUKI_MAILBOX->status)];
                snprintf(out, sizeof(out), "cm33: %s", line);
                status(out);
                n = 0U;
            }
        }
        else if (n < (sizeof(line) - 1U))
        {
            line[n++] = c;
        }
    }
    return len;
}
