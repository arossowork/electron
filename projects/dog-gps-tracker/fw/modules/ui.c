/* module ui: status LED, button, antiphase buzzer.
 * emits: LOST_CMD; owns: LED, BUZ_P, BUZ_N, BTN. LED average <= 2 uA: pulses of led_on_ms every led_period_s, at most 3. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"

enum { T_PERIOD = 1, T_LED, T_DEBOUNCE, T_LONG, T_BUZZ };
#define US(s) ((uint64_t)(s) * 1000000ull)
#define MS(m) ((uint64_t)(m) * 1000ull)

static int pulses(const ui_ctx_t *c)
{
    int n = 1 + c->low_batt + (c->link_mode == 0 ? 1 : 0);   /* 1 ok, +1 low battery, +1 no link yet */
    return n > 3 ? 3 : n;
}

static void pulse_start(fwk_task_t *t, ui_ctx_t *c, int n, int on_ms)
{
    if (c->pulses_left)
        return;                                              /* a pattern is running */
    c->pulses_left = n;
    c->on_ms = on_ms;
    c->pulse_on = 1;
    board_led(1);
    fwk_timer_start(t, T_LED, MS(on_ms), 0);
}

static void buzz_stop(fwk_task_t *t, ui_ctx_t *c)
{
    fwk_timer_stop(t, T_BUZZ);
    board_buz_stop();                                        /* both legs low: never DC (F3) */
    if (c->buzzing)
        fwk_stay_awake(t, 0);
    c->buzzing = 0;
}

int mod_ui_task(fwk_task_t *t, const fwk_event_t *e)
{
    ui_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        board_led(0);
        board_buz_stop();
        c->link_mode = 0;
        fwk_timer_start(t, T_PERIOD, US(BOARD_P_LED_PERIOD_S), US(BOARD_P_LED_PERIOD_S));
        break;
    case EV_BTN_EDGE:
        if (e->a == BOARD_BTN_ACTIVE) {
            fwk_timer_start(t, T_DEBOUNCE, MS(BOARD_P_BTN_DEBOUNCE_MS), 0);
        } else if (c->btn_down) {                            /* released */
            c->btn_down = 0;
            fwk_timer_stop(t, T_LONG);
            if (!c->long_fired)
                pulse_start(t, c, pulses(c), BOARD_P_BTN_STATUS_MS);
            c->long_fired = 0;
        }
        break;
    case EV_BUZZ_CMD: {
        int s = e->a > BOARD_P_BUZZER_ON_S ? BOARD_P_BUZZER_ON_S : e->a;   /* 10 s pattern, then off */
        if (s <= 0) {
            buzz_stop(t, c);
            break;
        }
        if (!c->buzzing)
            fwk_stay_awake(t, 1);
        c->buzzing = 1;
        {
            uint32_t refresh, loop;
            if (!buz_pwm_plan(BOARD_P_BUZ_FREQ_HZ, (uint32_t)s * 1000u, &refresh, &loop)) {   /* not a finite sequence: stay silent */
                buzz_stop(t, c);
                break;
            }
        }
        board_buz_start(BOARD_P_BUZ_FREQ_HZ, (uint32_t)s * 1000u);
        fwk_timer_start(t, T_BUZZ, US(s), 0);
        break;
    }
    case EV_LOST_STATE:
        c->lost_active = e->a == 1;
        break;
    case EV_LOW_BATT:
        c->low_batt = 1;
        break;
    case EV_BATT_REPORT:
        if (low_batt_clear(e->a))
            c->low_batt = 0;
        break;
    case EV_LINK_STATE:
        c->link_mode = e->a;
        break;
    case FWK_EV_TIMER:
        if (e->a == T_PERIOD)
            pulse_start(t, c, pulses(c), BOARD_P_LED_ON_MS);
        else if (e->a == T_LED) {
            if (c->pulse_on) {
                board_led(0);
                c->pulse_on = 0;
                if (--c->pulses_left > 0)
                    fwk_timer_start(t, T_LED, MS(BOARD_P_LED_GAP_MS), 0);
            } else {
                c->pulse_on = 1;
                board_led(1);
                fwk_timer_start(t, T_LED, MS(c->on_ms), 0);
            }
        } else if (e->a == T_DEBOUNCE) {
            if (board_btn_level() == BOARD_BTN_ACTIVE && !c->btn_down) {
                c->btn_down = 1;
                fwk_timer_start(t, T_LONG, US(BOARD_P_BTN_CANCEL_LOST_S), 0);
            }
        } else if (e->a == T_LONG) {
            if (c->btn_down && c->lost_active) {
                c->long_fired = 1;
                fwk_post(ev_lost_cmd(0));
            }
        } else if (e->a == T_BUZZ)
            buzz_stop(t, c);
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
