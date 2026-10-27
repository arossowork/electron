/* power: battery reading and the guard sequence (rule 8), cut-off (R23); sole owner of VBAT_EN and SENS_EN.
 * Other modules ask for a rail with EV_HOLD and wait for EV_HOLD_READY. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "fwk_hal.h"
#include "modules.h"

enum { T_TICK = 1, T_RECHARGE = 2 };

static int vbat_read(void) { return (int)(board_adc_mv(BOARD_ADC_VBAT_ADC) * BOARD_VBAT_DIV_RATIO + 0.5f); }

static void guard_release(power_ctx_t *c)        /* rule 8e */
{
    board_gpio_out(BOARD_GPIO_VBAT_EN, !BOARD_VBAT_EN_ACTIVE);
    board_wait_us(BOARD_GUARD_RELEASE_US);
    board_gpio_hiz(BOARD_GPIO_VBAT_EN);
    c->guard_on = 0;
}

static void hold(power_ctx_t *c, const fwk_event_t *e)
{
    if (e->a & RES_SENS) {
        if (e->b) {
            if (c->sens_cnt++ == 0)
                board_gpio_out(BOARD_GPIO_SENS_EN, BOARD_SENS_EN_ACTIVE);
            fwk_post(ev_hold_ready(RES_SENS));
        } else if (c->sens_cnt && --c->sens_cnt == 0) {
            board_gpio_out(BOARD_GPIO_SENS_EN, !BOARD_SENS_EN_ACTIVE);
        }
    }
    if (e->a & RES_GUARD) {
        if (e->b) {
            c->guard_cnt++;
            c->guard_req = 1;                          /* served by the sequence below */
        } else if (c->guard_cnt && --c->guard_cnt == 0 && c->guard_on) {
            guard_release(c);
        }
    }
}

int mod_power_task(fwk_task_t *t, const fwk_event_t *e)
{
    power_ctx_t *c = t->ctx;
    if (e->type == FWK_EV_INIT)
        fwk_timer_start(t, T_TICK, 1000, (uint64_t)BOARD_CONTROL_PERIOD_S * 1000000u);
    else if (e->type == EV_HOLD)
        hold(c, e);
    else if (e->type == FWK_EV_TIMER && e->a == T_TICK)
        c->tick = 1;

    CO_BEGIN(t);
    for (;;) {
        CO_WAIT_UNTIL(t, c->tick || c->guard_req);
        if (!c->guard_on) {                            /* rule 8b: two samples inside the 50 us window */
            board_gpio_out(BOARD_GPIO_VBAT_EN, BOARD_VBAT_EN_ACTIVE);
            board_wait_us(20);
            c->s1 = vbat_read();
            board_wait_us(20);
            c->s2 = vbat_read();
            if (c->s1 - c->s2 > BOARD_REVERSE_DROP_MV) {
                guard_release(c);
                c->tick = c->guard_req = 0;
                fwk_post(ev_reverse_fault());
                continue;
            }
            if (bat_guard_wanted(&c->bat, c->s1)) {    /* rule 8c: recharge through the guard, then read */
                board_gpio_hold(BOARD_GPIO_VBAT_EN, 1);
                fwk_timer_start(t, T_RECHARGE, BOARD_GUARD_RECHARGE_US, 0);
                CO_AWAIT(t, e->type == FWK_EV_TIMER && e->a == T_RECHARGE);
                board_gpio_hold(BOARD_GPIO_VBAT_EN, 0);
                c->mv = vbat_read();
                bat_store(&c->bat, 1, c->mv);
                c->guard_on = c->mv < BOARD_GUARD_ON_BELOW_MV;
                if (!c->guard_on)
                    guard_release(c);
            } else {                                   /* rule 8d: the 50 us read only, guard OFF */
                guard_release(c);
                c->mv = c->s2;
                bat_store(&c->bat, 0, c->mv);
            }
        }
        if (c->tick) {
            fwk_post(ev_vbat(c->mv));
            int r = bat_update(&c->bat, c->mv);
            if (r == BAT_CUTOFF_NOW)
                fwk_post(ev_bat_cutoff(c->mv));
            else if (r == BAT_WARN)
                fwk_post(ev_bat_warn(c->mv));
        }
        if (c->guard_req)
            fwk_post(ev_hold_ready(RES_GUARD));        /* ON for alkaline; lithium runs from the regulated rail */
        if (c->guard_on && c->guard_cnt == 0)
            guard_release(c);                          /* rule 8e: the guard does not outlive the wake */
        c->tick = c->guard_req = 0;
    }
    CO_END(t);
}
