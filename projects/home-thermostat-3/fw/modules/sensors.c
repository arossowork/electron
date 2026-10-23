/* sensors: both NTC probes through the ADS1115 every BOARD_PROBE_PERIOD_S (D17), at BOARD_ADS_SPS (rule 12).
 * Conversions wait on timers, so the CPU sleeps through them. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"

enum { T_READ = 1, T_CONV = 2 };
static const int AIN[3] = {BOARD_ADS_IN_REF, BOARD_ADS_IN_NTC1, BOARD_ADS_IN_NTC2};

static int start(int ain)                          /* single shot, +-4.096 V, data rate from BOARD_ADS_SPS */
{
    static const int dr[8] = {8, 16, 32, 64, 128, 250, 475, 860};
    int i = 0;
    while (i < 7 && dr[i] < BOARD_ADS_SPS)
        i++;
    uint16_t cfg = (uint16_t)(0x8000u | ((4u + (unsigned)ain) << 12) | (1u << 9) | (1u << 8) | ((unsigned)i << 5) | 3u);
    uint8_t w[3] = {0x01, (uint8_t)(cfg >> 8), (uint8_t)cfg};
    return board_i2c_write(BOARD_I2C_ADDR_ADS, w, 3);
}

static int done(void)
{
    uint8_t p = 0x01, r[2];
    return !board_i2c_write(BOARD_I2C_ADDR_ADS, &p, 1) && !board_i2c_read(BOARD_I2C_ADDR_ADS, r, 2) && (r[0] & 0x80);
}

static int16_t result(void)
{
    uint8_t p = 0x00, r[2] = {0, 0};
    if (board_i2c_write(BOARD_I2C_ADDR_ADS, &p, 1) || board_i2c_read(BOARD_I2C_ADDR_ADS, r, 2))
        return -1;
    return (int16_t)((r[0] << 8) | r[1]);
}

int mod_sensors_task(fwk_task_t *t, const fwk_event_t *e)
{
    sensors_ctx_t *c = t->ctx;
    if (e->type == FWK_EV_INIT)
        fwk_timer_start(t, T_READ, 2000, (uint64_t)BOARD_PROBE_PERIOD_S * 1000000u);

    CO_BEGIN(t);
    for (;;) {
        CO_AWAIT(t, e->type == FWK_EV_TIMER && e->a == T_READ);
        fwk_post(ev_hold(RES_SENS, 1));
        CO_AWAIT(t, e->type == EV_HOLD_READY && (e->a & RES_SENS));
        for (c->ch = 0; c->ch < 3; c->ch++) {
            start(AIN[c->ch]);
            fwk_timer_start(t, T_CONV, 1000000u / BOARD_ADS_SPS, 0);
            CO_AWAIT(t, e->type == FWK_EV_TIMER && e->a == T_CONV);
            while (!done()) {                          /* the data rate has a tolerance */
                fwk_timer_start(t, T_CONV, 1000, 0);
                CO_AWAIT(t, e->type == FWK_EV_TIMER && e->a == T_CONV);
            }
            if (c->ch == 0)
                c->ref = result();
            else
                c->code[c->ch - 1] = result();
        }
        board_gpio_hiz(BOARD_GPIO_SDA);                /* no back-power into U3 once the rail drops */
        board_gpio_hiz(BOARD_GPIO_SCL);
        fwk_post(ev_hold(RES_SENS, 0));
        for (int i = 0; i < NROOM; i++) {
            uint32_t q = 0;
            int st = ntc_ratio_q16(c->code[i], c->ref, &q);
            fwk_post(st == NTC_OK ? ev_temp(i, ntc_temp_mc(q)) : ev_probe_fault(i, st));
        }
    }
    CO_END(t);
}
