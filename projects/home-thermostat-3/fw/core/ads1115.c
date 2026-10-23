#include "ads1115.h"
#include "board.h"
#include "hal.h"

/* ADS1115 registers and config fields (TI SBAS444, register map) */
#define REG_CONV 0x00
#define REG_CFG  0x01
#define CFG_OS        (1u << 15)
#define CFG_MUX_SE(n) ((uint16_t)((4u + (n)) << 12))  /* AINn against GND */
#define CFG_PGA_4V096 (1u << 9)                       /* +-4.096 V: the smallest range above the 3.3 V excitation */
#define CFG_MODE_SS   (1u << 8)                       /* single shot, then power-down */
#define CFG_COMP_OFF  0x0003u

static uint16_t dr_bits(int sps)
{
    static const int table[8] = {8, 16, 32, 64, 128, 250, 475, 860};
    for (int i = 0; i < 8; i++)
        if (table[i] >= sps)
            return (uint16_t)(i << 5);
    return 7u << 5;
}

int ads_read(int ain, int16_t *code)
{
    uint16_t cfg = CFG_OS | CFG_MUX_SE(ain) | CFG_PGA_4V096 | CFG_MODE_SS | dr_bits(BOARD_ADS_SPS) | CFG_COMP_OFF;
    uint8_t w[3] = {REG_CFG, (uint8_t)(cfg >> 8), (uint8_t)cfg};
    if (hal_i2c_write(BOARD_I2C_ADDR_ADS, w, 3))
        return -1;
    hal_light_sleep_us(1000000u / BOARD_ADS_SPS);     /* one conversion period (rule 12 data rate) */
    for (int tries = 0; tries < 20; tries++) {         /* then poll OS: the data rate has a tolerance */
        uint8_t p = REG_CFG, r[2];
        if (hal_i2c_write(BOARD_I2C_ADDR_ADS, &p, 1) || hal_i2c_read(BOARD_I2C_ADDR_ADS, r, 2))
            return -1;
        if (r[0] & 0x80)
            break;
        hal_light_sleep_us(1000);
    }
    uint8_t p = REG_CONV, r[2];
    if (hal_i2c_write(BOARD_I2C_ADDR_ADS, &p, 1) || hal_i2c_read(BOARD_I2C_ADDR_ADS, r, 2))
        return -1;
    *code = (int16_t)((r[0] << 8) | r[1]);
    return 0;
}
