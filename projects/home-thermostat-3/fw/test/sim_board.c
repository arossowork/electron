#include "sim_board.h"
#include <string.h>
#include "board.h"
#include "board_hal.h"
#include "events.h"
#include "fwk_sim.h"

sim_board_t sb;
static struct { uint16_t cfg, ptr; uint64_t start; } ads;

void sim_board_reset(int vbat_mv, int temp_c)
{
    memset(&sb, 0, sizeof sb);
    memset(&ads, 0, sizeof ads);
    sb.vbat_mv = vbat_mv;
    sb.temp_c[0] = sb.temp_c[1] = temp_c;
    sb.level[BOARD_GPIO_VBAT_EN] = 1;
    sb.level[BOARD_GPIO_LED_N] = 1;
}

static int is_k(int g)
{
    return g == BOARD_GPIO_K1_IN1 || g == BOARD_GPIO_K1_IN2 || g == BOARD_GPIO_K2_IN1 || g == BOARD_GPIO_K2_IN2;
}

static void edge(int g, int level, int hiz)
{
    uint64_t now = fsim.now;
    int was = sb.hiz[g] ? -1 : sb.level[g], is = hiz ? -1 : level;
    if (g == BOARD_GPIO_SENS_EN) {
        if (was != 1 && is == 1)
            sb.sens_on_t = now;
        if (was == 1 && is != 1) {
            sb.sens_on_last_us = now - sb.sens_on_t;
            if (sb.sens_on_last_us > sb.sens_on_max_us)
                sb.sens_on_max_us = sb.sens_on_last_us;
        }
    }
    if (g == BOARD_GPIO_VBAT_EN) {             /* active low: the guard and the divider are on */
        if (was != 0 && is == 0)
            sb.vbat_en_low_t = now;
        if (was == 0 && is != 0 && now - sb.vbat_en_low_t > sb.vbat_en_low_max_us)
            sb.vbat_en_low_max_us = now - sb.vbat_en_low_t;
    }
    if (g == BOARD_GPIO_LED_N) {
        if (was != 0 && is == 0)
            sb.led_on_t = now;
        if (was == 0 && is != 0)
            sb.led_on_us += now - sb.led_on_t;
    }
    sb.level[g] = level;
    sb.hiz[g] = hiz;
}

void board_gpio_out(int g, int level)
{
    if (is_k(g))
        sb.plain_k_drive++;
    edge(g, level != 0, 0);
}
void board_gpio_hiz(int g) { edge(g, sb.level[g], 1); }
void board_gpio_hold(int g, int on) { (void)g; (void)on; }
void board_wait_us(uint32_t us) { fwk_sim_advance(us); }
int board_wifi_tx_busy(void) { return 0; }

int board_adc_mv(int ch)
{
    if (ch != BOARD_ADC_VBAT_ADC || sb.hiz[BOARD_GPIO_VBAT_EN] || sb.level[BOARD_GPIO_VBAT_EN])
        return 0;                              /* divider off */
    return (int)(sb.vbat_mv / BOARD_VBAT_DIV_RATIO);
}

/* ---- ADS1115 (TI SBAS444): pointer 0 = conversion, 1 = config; OS bit 15 starts / reports done */
static int rail(void) { return !sb.hiz[BOARD_GPIO_SENS_EN] && sb.level[BOARD_GPIO_SENS_EN]; }

static int16_t code(int ain)
{
    const int16_t ref = 26000;
    if (ain == BOARD_ADS_IN_REF)
        return ref;
    int i = ain == BOARD_ADS_IN_NTC1 ? 0 : 1;
    if (sb.probe_open[i])
        return ref;
    int idx = (sb.temp_c[i] - BOARD_NTC_T_MIN_C) / BOARD_NTC_T_STEP_C;
    return (int16_t)((int32_t)ref * BOARD_NTC_TABLE_Q16[idx] / 65536);
}

int board_i2c_write(uint8_t addr, const uint8_t *b, size_t n)
{
    if (!rail()) {
        sb.i2c_without_rail++;
        return -1;
    }
    if (addr != BOARD_I2C_ADDR_ADS || n < 1)
        return -1;
    ads.ptr = b[0];
    if (n == 3 && b[0] == 1) {
        ads.cfg = (uint16_t)((b[1] << 8) | b[2]);
        ads.start = fsim.now;
    }
    return 0;
}

int board_i2c_read(uint8_t addr, uint8_t *b, size_t n)
{
    if (!rail() || addr != BOARD_I2C_ADDR_ADS || n != 2)
        return -1;
    int ready = fsim.now - ads.start >= 1000000u / BOARD_ADS_SPS;
    uint16_t v = ads.ptr == 1 ? (uint16_t)(ads.cfg & 0x7FFF) | (ready ? 0x8000 : 0)
                              : (uint16_t)code(((ads.cfg >> 12) & 7) - 4);
    b[0] = (uint8_t)(v >> 8);
    b[1] = (uint8_t)v;
    return 0;
}

/* ---- the RMT-timed pulse: runs on its own; the end-of-transmission interrupt posts PULSE_DONE */
int board_pulse_start(int relay, int hi, int lo, uint32_t drive_us, uint32_t brake_us)
{
    (void)lo;
    if (sb.pulses < 16) {
        sb.pulse_relay[sb.pulses] = relay;
        sb.pulse_hi[sb.pulses] = hi;
        sb.pulse_t[sb.pulses] = fsim.now;
    }
    sb.pulses++;
    sb.coil_us_total += drive_us;              /* the peripheral ends the drive whatever the CPU does */
    fwk_sim_inject_at(fsim.now + drive_us + brake_us, ev_pulse_done(relay));
    if (sb.hang_in_pulse_us) {                 /* one hang; the coil time is read when the watchdog bites */
        fwk_sim_advance((uint64_t)sb.hang_in_pulse_us);
        sb.hang_in_pulse_us = 0;
        sb.coil_us_at_hang_end = sb.coil_us_total;
    }
    return 0;
}
