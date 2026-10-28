/*******************************************************************************
* File Name        : main.c
*
* Description      : This source file contains the main routine for non-secure
*                    application in the CM33 CPU
*
* Related Document : See README.md
*
********************************************************************************
* (c) 2025-2026, Infineon Technologies AG, or an affiliate of Infineon
* Technologies AG. All rights reserved.
* This software, associated documentation and materials ("Software") is
* owned by Infineon Technologies AG or one of its affiliates ("Infineon")
* and is protected by and subject to worldwide patent protection, worldwide
* copyright laws, and international treaty provisions. Therefore, you may use
* this Software only as provided in the license agreement accompanying the
* software package from which you obtained this Software. If no license
* agreement applies, then any use, reproduction, modification, translation, or
* compilation of this Software is prohibited without the express written
* permission of Infineon.
*
* Disclaimer: UNLESS OTHERWISE EXPRESSLY AGREED WITH INFINEON, THIS SOFTWARE
* IS PROVIDED AS-IS, WITH NO WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
* INCLUDING, BUT NOT LIMITED TO, ALL WARRANTIES OF NON-INFRINGEMENT OF
* THIRD-PARTY RIGHTS AND IMPLIED WARRANTIES SUCH AS WARRANTIES OF FITNESS FOR A
* SPECIFIC USE/PURPOSE OR MERCHANTABILITY.
* Infineon reserves the right to make changes to the Software without notice.
* You are responsible for properly designing, programming, and testing the
* functionality and safety of your intended application of the Software, as
* well as complying with any legal requirements related to its use. Infineon
* does not guarantee that the Software will be free from intrusion, data theft
* or loss, or other breaches ("Security Breaches"), and Infineon shall have
* no liability arising out of any Security Breaches. Unless otherwise
* explicitly approved by Infineon, the Software may not be used in any
* application where a failure of the Product or any consequences of the use
* thereof can reasonably be expected to result in personal injury.
*******************************************************************************/

/*******************************************************************************
* Header Files
*******************************************************************************/

#include <string.h>
#include <time.h>

#include "cybsp.h"
#include "ibuki_mailbox.h"
#include "ibuki_wifi.h"
#include "FreeRTOS.h"
#include "task.h"
#include "cyabs_rtos.h"
#include "cyabs_rtos_impl.h"
#include "cy_time.h"
#include "mtb_hal.h"

/* FreeRTOS's tickless idle puts the CM33 into deep sleep; this low-power timer wakes it again.
 * Without it the CM33 sleeps for good (2026-10-28: the Wi-Fi task stopped silently). Set up
 * exactly as in Infineon's mtb-example-psoc-edge-wifi-https-client. */
#define LPTIMER_0_WAIT_TIME_USEC        (62U)
#define APP_LPTIMER_INTERRUPT_PRIORITY  (1U)
static mtb_hal_lptimer_t lptimer_obj;
static mtb_hal_rtc_t rtc_obj;

static void lptimer_interrupt_handler(void)
{
    mtb_hal_lptimer_process_interrupt(&lptimer_obj);
}

static bool setup_tickless_idle_timer(void)
{
    cy_stc_sysint_t cfg = {.intrSrc = CYBSP_CM33_LPTIMER_0_IRQ, .intrPriority = APP_LPTIMER_INTERRUPT_PRIORITY};
    if (Cy_SysInt_Init(&cfg, lptimer_interrupt_handler) != CY_SYSINT_SUCCESS)
    {
        return false;
    }
    NVIC_EnableIRQ(cfg.intrSrc);
    if (Cy_MCWDT_Init(CYBSP_CM33_LPTIMER_0_HW, &CYBSP_CM33_LPTIMER_0_config) != CY_MCWDT_SUCCESS)
    {
        return false;
    }
    Cy_MCWDT_Enable(CYBSP_CM33_LPTIMER_0_HW, CY_MCWDT_CTR_Msk, LPTIMER_0_WAIT_TIME_USEC);
    if (mtb_hal_lptimer_setup(&lptimer_obj, &CYBSP_CM33_LPTIMER_0_hal_config) != CY_RSLT_SUCCESS)
    {
        return false;
    }
    cyabs_rtos_set_lptimer(&lptimer_obj);
    return true;
}

/* the RTC behind the C library's time, as the example does. TLS checks certificate dates, and
 * the board's default date (1 January 2024) is before the university server's certificate was
 * issued, so the login failed as "not yet valid". The board has no clock battery and no network
 * time before it joins, so the clock starts at the time this firmware was built: close enough for
 * certificates valid for years. */
static void setup_clib_support(void)
{
    static const char months[] = "JanFebMarAprMayJunJulAugSepOctNovDec";
    const char* d = __DATE__; /* "Oct 28 2026" */
    const char* t = __TIME__; /* "09:41:07" */
    uint32_t month = 1U;
    for (uint32_t i = 0U; i < 12U; i++)
    {
        if (strncmp(&months[i * 3U], d, 3U) == 0)
        {
            month = i + 1U;
        }
    }
    struct tm now;
    memset(&now, 0, sizeof(now));
    now.tm_mday = ((d[4] == ' ') ? 0 : (d[4] - '0')) * 10 + (d[5] - '0');
    now.tm_mon = (int)month - 1;
    now.tm_year = ((d[7] - '0') * 1000 + (d[8] - '0') * 100 + (d[9] - '0') * 10 + (d[10] - '0')) - 1900;
    now.tm_hour = (t[0] - '0') * 10 + (t[1] - '0');
    now.tm_min = (t[3] - '0') * 10 + (t[4] - '0');
    now.tm_sec = (t[6] - '0') * 10 + (t[7] - '0');
    Cy_RTC_Init(&CYBSP_RTC_config);
    /* through the HAL, not the PDL: the RTC keeps only two digits of the year, and the HAL stores
     * the century separately (a backup register) only when it writes the time itself. Set with the
     * PDL, the C library read the year as 26 AD and every certificate as not yet valid. */
    (void)mtb_hal_rtc_write(&rtc_obj, &now);
    mtb_clib_support_init(&rtc_obj);
}

/* the Wi-Fi task, as Infineon's HTTPS client example sizes it */
#define WIFI_TASK_STACK_SIZE    (10U * 1024U)
#define WIFI_TASK_PRIORITY      (1U)

/*******************************************************************************
* Macros
*******************************************************************************/
/* The timeout value in microseconds used to wait for CM55 core to be booted */
#define CM55_BOOT_WAIT_TIME_USEC    (10U)

/* App boot address for CM55 project */
#define CM55_APP_BOOT_ADDR          (CYMEM_CM33_0_m55_nvm_START + \
                                        CYBSP_MCUBOOT_HEADER_SIZE)

/*******************************************************************************
* Function Name: handle_app_error
********************************************************************************
* Summary:
* User defined error handling function
*
* Parameters:
*  void
*
* Return:
*  void
*
*******************************************************************************/
__STATIC_INLINE void handle_app_error(void)
{
    /* Disable all interrupts. */
    __disable_irq();

    CY_ASSERT(0);

    /* Infinite loop */
    while(true);
}

/*******************************************************************************
* Function Name: main
********************************************************************************
* Summary:
*  This is the main function. It initializes the system and boots the CM55 CPU.
*  and it enters Deepsleep mode.

*
* Parameters:
*  none
*
* Return:
*  int
*
*******************************************************************************/
int main(void)
{
    cy_rslt_t result = CY_RSLT_SUCCESS;

    /* Initialize the device and board peripherals. */
    result = cybsp_init();

    /* Board initialization failed. Stop program execution. */
    if (CY_RSLT_SUCCESS != result)
    {
        handle_app_error();
    }

    /* Enable global interrupts */
    __enable_irq();

    setup_clib_support();
    bool lptimer_ok = setup_tickless_idle_timer();

    /* IBUKI: clear the mailbox shared with the M55 before it starts, then say hello through it */
    memset((void*)IBUKI_MAILBOX, 0, sizeof(ibuki_mailbox_t));
    IBUKI_MAILBOX->magic = IBUKI_MAILBOX_MAGIC;
    strncpy((char*)IBUKI_MAILBOX->status, lptimer_ok ? "cm33: mailbox ready" : "cm33: low-power timer setup failed",
            sizeof(IBUKI_MAILBOX->status) - 1U);
    IBUKI_MAILBOX->status_seq = 1U;

    /* Enable CM55. */
    /* CY_CM55_APP_BOOT_ADDR must be updated if CM55 memory layout is changed.*/
    Cy_SysEnableCM55(MXCM55, CM55_APP_BOOT_ADDR, CM55_BOOT_WAIT_TIME_USEC);

    /* IBUKI: the CM33 runs the board's own phone alerts over Wi-Fi (ibuki_wifi.c) */
    if (xTaskCreate(ibuki_wifi_task, "IBUKI Wi-Fi", WIFI_TASK_STACK_SIZE, NULL, WIFI_TASK_PRIORITY, NULL) == pdPASS)
    {
        vTaskStartScheduler(); /* does not return */
    }
    strncpy((char*)IBUKI_MAILBOX->status, "wifi: task not started", sizeof(IBUKI_MAILBOX->status) - 1U);
    IBUKI_MAILBOX->status_seq = IBUKI_MAILBOX->status_seq + 1U;
    for(;;)
    {
        Cy_SysPm_CpuEnterDeepSleep(CY_SYSPM_WAIT_FOR_INTERRUPT);
    }
}

/* [] END OF FILE */