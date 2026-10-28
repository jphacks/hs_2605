/* The board's other sensors (see sensors.h), with the PDL's I2C controller functions. */
#include "sensors.h"

#include "cybsp.h"
#include "ibuki.h"
#include "lines.h"

#define I2C_TIMEOUT_MS (5U)

static cy_stc_scb_i2c_context_t i2c;
static bool i2c_ok;

/* true if a chip acknowledges its address */
static bool answers(uint32_t address)
{
    bool ack = Cy_SCB_I2C_MasterSendStart(CYBSP_I2C_CONTROLLER_HW, address, CY_SCB_I2C_WRITE_XFER,
                                          I2C_TIMEOUT_MS, &i2c) == CY_SCB_I2C_SUCCESS;
    (void)Cy_SCB_I2C_MasterSendStop(CYBSP_I2C_CONTROLLER_HW, I2C_TIMEOUT_MS, &i2c);
    return ack;
}

/* write one command byte, wait, read n bytes; false if the chip doesn't answer */
static bool command_read(uint32_t address, uint8_t cmd, uint32_t wait_ms, uint8_t* out, uint32_t n)
{
    bool ok = Cy_SCB_I2C_MasterSendStart(CYBSP_I2C_CONTROLLER_HW, address, CY_SCB_I2C_WRITE_XFER,
                                         I2C_TIMEOUT_MS, &i2c) == CY_SCB_I2C_SUCCESS
              && Cy_SCB_I2C_MasterWriteByte(CYBSP_I2C_CONTROLLER_HW, cmd, I2C_TIMEOUT_MS, &i2c) == CY_SCB_I2C_SUCCESS;
    (void)Cy_SCB_I2C_MasterSendStop(CYBSP_I2C_CONTROLLER_HW, I2C_TIMEOUT_MS, &i2c);
    if (!ok)
    {
        return false;
    }
    Cy_SysLib_Delay(wait_ms);
    ok = Cy_SCB_I2C_MasterSendStart(CYBSP_I2C_CONTROLLER_HW, address, CY_SCB_I2C_READ_XFER,
                                    I2C_TIMEOUT_MS, &i2c) == CY_SCB_I2C_SUCCESS;
    for (uint32_t i = 0U; ok && (i < n); i++)
    {
        ok = Cy_SCB_I2C_MasterReadByte(CYBSP_I2C_CONTROLLER_HW, (i + 1U < n) ? CY_SCB_I2C_ACK : CY_SCB_I2C_NAK,
                                       &out[i], I2C_TIMEOUT_MS, &i2c) == CY_SCB_I2C_SUCCESS;
    }
    (void)Cy_SCB_I2C_MasterSendStop(CYBSP_I2C_CONTROLLER_HW, I2C_TIMEOUT_MS, &i2c);
    return ok;
}

#define SHT_ADDRESS   (0x44U)   /* answered with a Sensirion SHT4x serial number and valid CRCs */
#define CLIMATE_MS    (10000U)  /* one reading every 10 s */
static uint32_t last_climate_ms;

/* the CRC-8 the SHT4x puts after every 2 data bytes (polynomial 0x31, start 0xFF) */
static uint8_t crc8(const uint8_t* d)
{
    uint8_t c = 0xFFU;
    for (uint32_t i = 0U; i < 2U; i++)
    {
        c ^= d[i];
        for (uint32_t b = 0U; b < 8U; b++)
        {
            c = (c & 0x80U) ? (uint8_t)((c << 1) ^ 0x31U) : (uint8_t)(c << 1);
        }
    }
    return c;
}

/* "H,t_ms,<temperature C>,<relative humidity %>" from one high-precision measurement (0xFD,
 * ~9 ms). The sensor sits on the board, beside the radar and the processor, so it reads the
 * board's temperature: warmer than the room (36 C in a ~25 C room on 2026-10-28). */
static void send_climate(uint32_t now)
{
    uint8_t b[6];
    if (!command_read(SHT_ADDRESS, 0xFDU, 10U, b, 6U) || (crc8(&b[0]) != b[2]) || (crc8(&b[3]) != b[5]))
    {
        return; /* no reading this time */
    }
    int32_t t10 = -450 + (int32_t)((1750U * ((uint32_t)b[0] << 8 | b[1])) / 65535U);
    int32_t h10 = -60 + (int32_t)((1250U * ((uint32_t)b[3] << 8 | b[4])) / 65535U);
    h10 = (h10 < 0) ? 0 : ((h10 > 1000) ? 1000 : h10);
    char* p = line_start();
    *p++ = 'H';
    *p++ = ',';
    p = line_put_u32(p, now);
    *p++ = ',';
    if (t10 < 0)
    {
        *p++ = '-';
        t10 = -t10;
    }
    p = line_put_tenths(p, (uint32_t)t10);
    *p++ = ',';
    p = line_put_tenths(p, (uint32_t)h10);
    line_send(p);
}

void sensors_poll(uint32_t now_ms)
{
    if (i2c_ok && ((now_ms - last_climate_ms) >= CLIMATE_MS))
    {
        last_climate_ms = now_ms;
        send_climate(now_ms);
    }
}

void sensors_init(void)
{
    i2c_ok = Cy_SCB_I2C_Init(CYBSP_I2C_CONTROLLER_HW, &CYBSP_I2C_CONTROLLER_config, &i2c) == CY_SCB_I2C_SUCCESS;
    if (!i2c_ok)
    {
        ibuki_status("i2c: init failed");
        return;
    }
    Cy_SCB_I2C_Enable(CYBSP_I2C_CONTROLLER_HW);
    char* p = line_start();
    *p++ = 'S';
    *p++ = ',';
    p = line_put_u32(p, ibuki_millis());
    p = line_put_text(p, ",i2c:");
    for (uint32_t a = 0x08U; a < 0x78U; a++) /* the usual 7-bit address range */
    {
        if (answers(a))
        {
            static const char hex[] = "0123456789abcdef";
            *p++ = ' ';
            *p++ = '0';
            *p++ = 'x';
            *p++ = hex[a >> 4];
            *p++ = hex[a & 15U];
        }
    }
    line_send(p);
}
