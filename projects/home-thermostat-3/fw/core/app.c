#include "app.h"
#include "ads1115.h"
#include "board.h"
#include "hal.h"

/* rule 8b sample times inside the 50 us window: the divider tau is 6.7 us (R15 || R16 x C15), so the first
 * sample at 3 tau and the second at 6 tau */
#define VBAT_S1_US 20
#define VBAT_S2_US 20
#if VBAT_S1_US + VBAT_S2_US > BOARD_VBAT_READ_WINDOW_US
#error "VBAT samples must fit the rule 8b window"
#endif

static const int ADS_AIN[RELAY_COUNT] = {BOARD_ADS_AIN_NTC1, BOARD_ADS_AIN_NTC2};

static int vbat_read(void)
{
    return bat_mv_from_adc(hal_adc1_mv(BOARD_ADC1_CH_VBAT_ADC));
}

static void guard_release(void)                   /* rule 8e: IO2 high >= 0.5 ms, then hi-Z */
{
    hal_gpio_out(BOARD_GPIO_VBAT_EN, !BOARD_VBAT_EN_ACTIVE);
    hal_wait_us(BOARD_GUARD_RELEASE_US);
    hal_gpio_hiz(BOARD_GPIO_VBAT_EN);
}

void app_cold_boot(app_rtc_t *s, const uint8_t stored[RELAY_COUNT])
{
    bat_cold_boot(&s->bat);
    relays_init(&s->rl);
    for (int i = 0; i < RELAY_COUNT; i++) {
        s->room[i].on = stored[i];
        s->room[i].changed = 0;
        s->room[i].last_change_s = 0;
        relays_request(&s->rl, i, stored[i]);     /* rule 6: the contact state is unknown after a cold boot */
    }
}

static void sensors(app_rtc_t *s, wake_report_t *o)
{
    int16_t ref = 0, code;
    if (ads_read(BOARD_ADS_AIN_REF, &ref))
        o->sensor_fault = 1;
    for (int i = 0; i < RELAY_COUNT; i++) {
        uint32_t q = 0;
        if (o->sensor_fault || ads_read(ADS_AIN[i], &code)) {
            o->sensor_fault = 1;
            o->st[i] = NTC_NO_REF;
            continue;
        }
        o->st[i] = ntc_ratio_q16(code, ref, &q);
        o->temp_mc[i] = o->st[i] == NTC_OK ? ntc_temp_mc(q) : 0;
    }
    (void)s;
}

void app_control_wake(app_rtc_t *s, uint32_t now_s, wake_report_t *o)
{
    *o = (wake_report_t){0};
    hal_wdt_feed();

    /* rule 8b: IO2 low, two samples inside the window; VBAT_P(off) is the first one (C1/C2 hold VBAT_P for ms) */
    hal_gpio_out(BOARD_GPIO_VBAT_EN, BOARD_VBAT_EN_ACTIVE);
    hal_wait_us(VBAT_S1_US);
    int s1 = vbat_read();
    hal_wait_us(VBAT_S2_US);
    int s2 = vbat_read();
    if (bat_reverse_suspect(s1, s2)) {
        guard_release();
        o->fault_reverse = 1;
        return;
    }
    o->guard_on = bat_guard_wanted(&s->bat, s1);
    if (o->guard_on) {                            /* rule 8c: recharge C1+C2 through the guard, then read */
        hal_gpio_hold(BOARD_GPIO_VBAT_EN, 1);
        hal_light_sleep_us(BOARD_GUARD_RECHARGE_US);
        hal_gpio_hold(BOARD_GPIO_VBAT_EN, 0);
        o->vbat_mv = vbat_read();
        bat_store_reading(&s->bat, 1, o->vbat_mv);
        if (o->vbat_mv >= BOARD_GUARD_ON_BELOW_MV) {
            guard_release();
            o->guard_on = 0;
        }
    } else {                                      /* rule 8d: the 50 us read only */
        guard_release();
        o->vbat_mv = s2;
        bat_store_reading(&s->bat, 0, s2);
    }

    o->bat = bat_update(&s->bat, o->vbat_mv);
    if (o->bat == BAT_CUTOFF_NOW)                 /* R23: both relays OFF from the cells, then no switching */
        for (int i = 0; i < RELAY_COUNT; i++)
            relays_request(&s->rl, i, 0);

    relays_pins_low();                            /* rule 1: IN pins low before nSLEEP (SENS_EN) goes high */
    uint64_t t_on = hal_now_us();
    hal_gpio_out(BOARD_GPIO_SENS_EN, BOARD_SENS_EN_ACTIVE);
    sensors(s, o);
    if (!s->bat.cutoff || o->bat == BAT_CUTOFF_NOW) {
        if (!s->bat.cutoff)
            for (int i = 0; i < RELAY_COUNT; i++) {
                int on = control_decide(&s->room[i], o->temp_mc[i], o->st[i], now_s, BOARD_RELAY_MIN_DWELL_S);
                if (on != s->rl.state[i] || s->rl.want[i] >= 0)
                    relays_request(&s->rl, i, on);
            }
        relays_run(&s->rl);
    }
    relays_pins_hiz();                            /* rule 2: inputs without pulls; DRV8837 pull-downs coast */
    hal_gpio_hiz(BOARD_GPIO_SDA);                 /* no back-power into U3 once SENS_EN is low */
    hal_gpio_hiz(BOARD_GPIO_SCL);
    hal_gpio_out(BOARD_GPIO_SENS_EN, !BOARD_SENS_EN_ACTIVE);
    o->sens_on_us = (uint32_t)(hal_now_us() - t_on);
    if (o->guard_on)
        guard_release();                          /* rule 8e at the end of every wake */
}
