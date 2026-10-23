/* actuators: latching relay pulses (R9), one at a time (rule 4), at least BOARD_RELAY_MIN_DWELL_S between two
 * switchings of one relay unless forced (D19), never during Wi-Fi TX. The pulse is timed by a peripheral, so a hang
 * cannot leave a coil driven (F2); its interrupt only posts EV_PULSE_DONE. Sole owner of the four bridge inputs. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "fwk_hal.h"
#include "modules.h"

enum { T_RETRY = 1, CMD_CLOSE = 1, CMD_FORCE = 2 };
static const int IN1[NROOM] = {BOARD_GPIO_K1_IN1, BOARD_GPIO_K2_IN1};
static const int IN2[NROOM] = {BOARD_GPIO_K1_IN2, BOARD_GPIO_K2_IN2};
static const int SET_IN[NROOM] = {BOARD_K1_DRV_SET_IN, BOARD_K2_DRV_SET_IN};

static void command(actuators_ctx_t *c, const fwk_event_t *e)
{
    int r = (int)e->a, close = (e->b & CMD_CLOSE) != 0, force = (e->b & CMD_FORCE) != 0;
    if (r < 0 || r >= NROOM)
        return;
    uint64_t now = fwk_hal_now_us(), dwell = (uint64_t)BOARD_RELAY_MIN_DWELL_S * 1000000u;
    if (!force && c->switched[r] && now - c->last_switch_us[r] < dwell) {
        fwk_post(ev_relay_deferred(r, (int32_t)((dwell - (now - c->last_switch_us[r])) / 1000000u)));
        return;
    }
    c->want[r] = (int8_t)close;
    c->force[r] = (uint8_t)force;
}

static int pending(const actuators_ctx_t *c)
{
    for (int i = 0; i < NROOM; i++)
        if (c->want[i] >= 0)
            return 1;
    return 0;
}

int mod_actuators_task(fwk_task_t *t, const fwk_event_t *e)
{
    actuators_ctx_t *c = t->ctx;
    if (e->type == FWK_EV_INIT)
        for (int i = 0; i < NROOM; i++)
            c->want[i] = -1;
    else if (e->type == EV_RELAY_CMD)
        command(c, e);
    else if (e->type == EV_HOLD_READY)
        c->ready |= (uint8_t)e->a;

    CO_BEGIN(t);
    for (;;) {
        CO_WAIT_UNTIL(t, pending(c));
        c->ready = 0;
        fwk_post(ev_hold(RES_SENS | RES_GUARD, 1));   /* nSLEEP high, guard ON for alkaline (R9 at 2.0 V) */
        CO_WAIT_UNTIL(t, (c->ready & (RES_SENS | RES_GUARD)) == (RES_SENS | RES_GUARD));
        fwk_stay_awake(t, 1);                         /* the pulse peripheral runs: light sleep only */
        while (pending(c)) {
            for (c->active = 0; c->want[c->active] < 0; c->active++)
                ;
            while (board_wifi_tx_busy()) {             /* rule 4 */
                fwk_timer_start(t, T_RETRY, 1000, 0);
                CO_AWAIT(t, e->type == FWK_EV_TIMER && e->a == T_RETRY);
            }
            {
                int r = c->active, set = c->want[r];
                int in1_high = set ? SET_IN[r] == 1 : SET_IN[r] != 1;
                board_pulse_start(r, in1_high ? IN1[r] : IN2[r], in1_high ? IN2[r] : IN1[r],
                                  BOARD_RELAY_PULSE_US, BOARD_RELAY_BRAKE_US);
            }
            CO_AWAIT(t, e->type == EV_PULSE_DONE && e->a == c->active);
            c->closed[c->active] = (uint8_t)c->want[c->active];
            c->want[c->active] = -1;
            c->switched[c->active] = 1;
            c->last_switch_us[c->active] = fwk_hal_now_us();
            fwk_post(ev_relay_state(c->active, c->closed[c->active]));
        }
        fwk_stay_awake(t, 0);
        fwk_post(ev_hold(RES_SENS | RES_GUARD, 0));
    }
    CO_END(t);
}
