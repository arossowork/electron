#include "sim_hal.h"
#include <math.h>
#include <string.h>
#include "board.h"
#include "hal.h"

sim_t sim;

static const int IN1[2] = {BOARD_GPIO_K1_IN1, BOARD_GPIO_K2_IN1};
static const int IN2[2] = {BOARD_GPIO_K1_IN2, BOARD_GPIO_K2_IN2};

#define TAU_RECHARGE_US 6600.0     /* C1+C2 through the guard and the cells (notes/circuit.md, sleep_floor) */
#define SET_TIME_US     15000      /* ADW1103HLW set/reset time max (notes/parts-230v.md) */
#define VEXC_MV         3300.0     /* SENS_EN = +3V3 */

void sim_reset(void)
{
    memset(&sim, 0, sizeof sim);
    sim.vbat_raw_mv = 3000;
    sim.vf_mv = 450;
    sim.temp_c[0] = sim.temp_c[1] = 20.0;
    sim.open_seg[0] = sim.open_seg[1] = -1;
    for (int i = 0; i < SIM_GPIO; i++)
        sim.level[i] = 0;
    sim.level[BOARD_GPIO_VBAT_EN] = 1;   /* R14 */
}

int sim_lithium(void) { return sim.vbat_raw_mv >= 3500; }

static int sens_on(void) { return sim.is_out[BOARD_GPIO_SENS_EN] && sim.level[BOARD_GPIO_SENS_EN]; }
static int io2_low(void) { return sim.is_out[BOARD_GPIO_VBAT_EN] && !sim.level[BOARD_GPIO_VBAT_EN]; }

static void track_io2(void)
{
    int low = io2_low();
    if (low && !sim.io2_low)
        sim.io2_low_t0 = sim.now;
    if (sim.io2_low) {
        uint64_t d = sim.now - sim.io2_low_t0;
        if (d > sim.io2_low_max_us)
            sim.io2_low_max_us = d;
    }
    sim.io2_low = low;
}

static void advance(uint64_t us)
{
    sim.now += us;
    track_io2();
}

static int bridge_mode(int r)
{
    if (!sens_on())
        return SEG_COAST;
    int a = sim.is_out[IN1[r]] && sim.level[IN1[r]], b = sim.is_out[IN2[r]] && sim.level[IN2[r]];
    return a && b ? SEG_BRAKE : a ? SEG_FWD : b ? SEG_REV : SEG_COAST;
}

static void track_relays(void)
{
    for (int r = 0; r < 2; r++) {
        int m = bridge_mode(r), o = sim.open_seg[r];
        if (o >= 0 && sim.seg[o].mode == m)
            continue;
        if (o >= 0) {
            sim_seg_t *s = &sim.seg[o];
            s->t1 = sim.now;
            sim.open_seg[r] = -1;
            if (s->t1 == s->t0 && o == sim.nseg - 1) {   /* a pin-ordering instant, not a drive */
                sim.nseg--;
                o = -1;
            }
        }
        if (o >= 0) {
            sim_seg_t *s = &sim.seg[o];
            if ((s->mode == SEG_FWD || s->mode == SEG_REV) && s->t1 - s->t0 >= SET_TIME_US)
                sim.contact[r] = (s->mode == SEG_FWD) == (BOARD_K1_SET_IN == 1);
            sim.open_seg[r] = -1;
        }
        if (m != SEG_COAST && sim.nseg < SIM_MAX_SEG) {
            sim.seg[sim.nseg] = (sim_seg_t){r, m, sens_on(), sim.now, 0};
            sim.open_seg[r] = sim.nseg++;
        }
    }
}

void hal_gpio_out(int g, int level)
{
    sim.is_out[g] = 1;
    sim.level[g] = level ? 1 : 0;
    track_io2();
    track_relays();
}

void hal_gpio_hiz(int g)
{
    sim.is_out[g] = 0;
    sim.level[g] = (g == BOARD_GPIO_VBAT_EN);    /* R14 pulls IO2 to VBAT_P; the others have pull-downs */
    track_io2();
    track_relays();
}

int  hal_gpio_in(int g) { return sim.level[g]; }
void hal_gpio_hold(int g, int on) { (void)g; (void)on; }
void hal_wait_us(uint32_t us) { advance(us); }
void hal_light_sleep_us(uint32_t us) { advance(us); }
uint64_t hal_now_us(void) { return sim.now; }
int  hal_wifi_tx_busy(void) { return sim.wifi_busy; }
void hal_wdt_feed(void) { sim.wdt_feeds++; }

static double vbat_p_mv(void)
{
    double off = sim.vbat_raw_mv - sim.vf_mv;
    if (!io2_low())
        return off;
    double t = (double)(sim.now - sim.io2_low_t0);
    if (sim.reversed)
        return off - 100.0 * t / 50.0;
    if (t <= 50.0)
        return off + sim.rise50_mv * t / 50.0;
    double v50 = off + sim.rise50_mv;
    return sim.vbat_raw_mv - (sim.vbat_raw_mv - v50) * exp(-(t - 50.0) / TAU_RECHARGE_US);
}

int hal_adc1_mv(int ch)
{
    if (ch != BOARD_ADC1_CH_VBAT_ADC) {
        sim.adc_bad_channel++;
        return 0;
    }
    if (!io2_low())
        return 0;                               /* M3 off: no divider current */
    return (int)lround(vbat_p_mv() * 10.0 / 30.0);   /* R16 / (R15 + R16), the real divider, not board.h */
}

static double ain_mv(int mux)
{
    if (mux == BOARD_ADS_AIN_REF)
        return VEXC_MV;
    for (int i = 0; i < 2; i++)
        if (mux == (i ? BOARD_ADS_AIN_NTC2 : BOARD_ADS_AIN_NTC1)) {
            if (sim.ntc_mode[i] == 1)
                return VEXC_MV;
            if (sim.ntc_mode[i] == 2)
                return 0.0;
            double r = 10000.0 * exp(3950.0 * (1.0 / (sim.temp_c[i] + 273.15) - 1.0 / 298.15));
            return VEXC_MV * r / (10000.0 + r);
        }
    return 0.0;
}

int hal_i2c_write(uint8_t addr, const uint8_t *b, size_t n)
{
    if (addr != 0x48)
        return -1;
    if (!sens_on()) {
        sim.i2c_unpowered++;
        return -1;
    }
    sim.ads_ptr = b[0];
    if (n == 3 && b[0] == 0x01) {
        sim.ads_cfg = (uint16_t)((b[1] << 8) | b[2]);
        static const int sps[8] = {8, 16, 32, 64, 128, 250, 475, 860};
        if (sim.ads_cfg & 0x8000)
            sim.ads_done = sim.now + 1000000u / sps[(sim.ads_cfg >> 5) & 7];
    }
    return 0;
}

int hal_i2c_read(uint8_t addr, uint8_t *b, size_t n)
{
    if (addr != 0x48 || n != 2)
        return -1;
    if (!sens_on()) {
        sim.i2c_unpowered++;
        return -1;
    }
    uint16_t v;
    if (sim.ads_ptr == 0x01) {
        v = (uint16_t)((sim.ads_cfg & 0x7FFF) | (sim.now >= sim.ads_done ? 0x8000 : 0));
    } else {
        int mux = ((sim.ads_cfg >> 12) & 7) - 4;
        long code = lround(ain_mv(mux) / 4096.0 * 32768.0);
        v = (uint16_t)(code > 32767 ? 32767 : code);
    }
    b[0] = (uint8_t)(v >> 8);
    b[1] = (uint8_t)v;
    return 0;
}
