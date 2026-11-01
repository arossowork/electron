/* module lost: owner-started, 1 fix/min, at most 2 h, at most max_lost_sessions_per_month sessions per 30 days (PWR-3, F6).
 * emits: POS_REQUEST, LOST_STATE. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"
#include "fwk_hal.h"

enum { T_TICK = 1 };
#define US(s) ((uint64_t)(s) * 1000000ull)

static void end(fwk_task_t *t, lost_ctx_t *c)
{
    fwk_timer_stop(t, T_TICK);
    c->active = 0;
    fwk_post(ev_lost_state(3, c->n_starts));
}

static void request(lost_ctx_t *c)
{
    c->moved = 0;
    c->last_req_us = fwk_hal_now_us();
    c->fixes++;
    fwk_post(ev_pos_request(1, ++c->seq));
}

int mod_lost_task(fwk_task_t *t, const fwk_event_t *e)
{
    lost_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        board_quota_load(c->starts, &c->n_starts);
        c->moved = 1;
        break;
    case EV_LOST_CMD:
        if (!e->a) {
            if (c->active)
                end(t, c);
            break;
        }
        if (c->active)
            break;
        {
            int today = board_day_index();
            if (!quota_allow(c->starts, c->n_starts, today)) {
                fwk_post(ev_lost_state(2, c->n_starts));            /* monthly limit: refused, not queued */
                break;
            }
            c->n_starts = quota_record(c->starts, c->n_starts, today);
            board_quota_save(c->starts, c->n_starts);
        }
        c->active = 1;
        c->fixes = 0;
        c->t_start_us = fwk_hal_now_us();
        fwk_post(ev_lost_state(1, c->n_starts));
        request(c);
        fwk_timer_start(t, T_TICK, US(BOARD_P_LOST_FIX_PERIOD_S), US(BOARD_P_LOST_FIX_PERIOD_S));
        break;
    case EV_MOTION:
        c->moved = 1;
        c->motion_seen = 1;
        break;
    case FWK_EV_TIMER:
        if (e->a == T_TICK && c->active) {
            uint64_t now = fwk_hal_now_us();
            if (now - c->t_start_us >= US(BOARD_P_LOST_MAX_DURATION_S)) {
                end(t, c);                                           /* 7200 s cap */
                break;
            }
            /* IF-9 (gating only once the sensor has proven itself with one MOTION event): a collar that has not moved needs no new fix, but a report at least every lost_still_max_s */
            if (!c->motion_seen || c->moved || now - c->last_req_us >= US(BOARD_P_LOST_STILL_MAX_S))
                request(c);
        }
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
