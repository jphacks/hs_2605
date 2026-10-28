/* The board's other sensors on its I2C bus (CYBSP_I2C_CONTROLLER, SCB0). Found on our board:
 * 0x18, 0x44, 0x68, 0x77. 0x44 is the temperature/humidity sensor (an SHT4x: its serial number
 * and measurements carry valid CRCs); the others are not read yet. */
#ifndef IBUKI_SENSORS_H
#define IBUKI_SENSORS_H

#include <stdint.h>

void sensors_init(void);             /* starts the bus, reports which chips answer: "S,t,i2c: 0x.. 0x.." */
void sensors_poll(uint32_t now_ms);  /* every 10 s: "H,t_ms,<temperature C>,<humidity %>" */

#endif /* IBUKI_SENSORS_H */
