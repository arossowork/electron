/* module conn: LTE-M with NB-IoT fallback, PSM + eDRX 81.92 s, 60-min poll fallback, request handling, upload, assistance.
 * emits: LINK_STATE, UPLOAD_DONE, BATT_CHECK, TX_BURST, POS_REQUEST, BUZZ_CMD, LOST_CMD, ASSIST_DONE. No pins: the modem is
 * behind board_hal.h. Every TX action (attach, upload, assistance) passes the battery gate first (PWR-7). */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "fwk_hal.h"
#include "events.h"

enum { T_ATTACH = 1, T_UPLOAD, T_ASSIST, T_POLL, T_BACKOFF, T_DEADLINE };
/* device allowance of IF-2: fix cap + upload cap, after the eDRX cycle and paging window (124.5 s <= 125 s) */
#define REQ_BUDGET_US US(BOARD_P_GNSS_FIX_TIMEOUT_S + BOARD_P_UPLOAD_TIMEOUT_S)
#define US(s) ((uint64_t)(s) * 1000000ull)

static void go_idle(fwk_task_t *t, conn_ctx_t *c);

static void gate(conn_ctx_t *c, int state, int reason)
{
    c->state = state;
    fwk_post(ev_batt_check(reason));
}

static void start_attach(fwk_task_t *t, conn_ctx_t *c)
{
    c->state = CS_ATTACH;
    fwk_post(ev_tx_burst(1));
    board_modem_attach(c->rat, 1);                              /* every attach asks for PSM + eDRX again (F2: retry at next attach) */
    fwk_timer_start(t, T_ATTACH, US(BOARD_P_ATTACH_TIMEOUT_S), 0);
}

static void attach_failed(fwk_task_t *t, conn_ctx_t *c)
{
    fwk_timer_stop(t, T_ATTACH);
    board_modem_abort();
    fwk_post(ev_tx_burst(0));
    if (c->rat == RAT_LTEM && !c->tried_nb) {                    /* IF-1: NB-IoT fallback */
        c->tried_nb = 1;
        c->rat = RAT_NBIOT;
        gate(c, CS_POLL_GATE, 1);
        return;
    }
    c->rat = RAT_LTEM;
    c->tried_nb = 0;
    c->mode = 0;
    fwk_post(ev_link_state(0, c->rat));
    c->state = CS_BACKOFF;                                       /* both RATs failed: try again in a poll period */
    fwk_timer_start(t, T_BACKOFF, US(BOARD_P_POLL_PERIOD_MIN * 60), 0);
}

static void begin_request(fwk_task_t *t, conn_ctx_t *c)
{
    (void)t;
    c->req_active = 1;
    c->t_req_us = fwk_hal_now_us();
    c->state = CS_WAIT_FIX;
    c->gate_ok_us = 0;
    fwk_post(ev_batt_check(2));                                  /* supercap recharge (2.7 s) overlaps the fix, not the upload */
    fwk_post(ev_pos_request(0, ++c->req_seq));                   /* gnss has 30 s from here (IF-2) */
}

static void start_upload(fwk_task_t *t, conn_ctx_t *c)
{
    c->state = CS_UPLOAD;
    c->fix_pending = 0;
    c->t_upload_us = fwk_hal_now_us();
    fwk_timer_stop(t, T_DEADLINE);
    uint64_t cap = US(BOARD_P_UPLOAD_TIMEOUT_S);
    if (c->req_active) {                                         /* the gate wait eats into the upload, never past the budget */
        uint64_t used = c->t_upload_us - c->t_req_us;
        uint64_t left = used < REQ_BUDGET_US ? REQ_BUDGET_US - used : 1;
        if (left < cap)
            cap = left;
    }
    fwk_post(ev_tx_burst(1));
    board_modem_upload(c->fix_valid, c->fix_age, c->idle_mv, c->loaded_mv, c->low_batt);
    fwk_timer_start(t, T_UPLOAD, cap, 0);
}

static void finish_upload(fwk_task_t *t, conn_ctx_t *c, int ok)
{
    fwk_timer_stop(t, T_UPLOAD);
    if (!ok)
        board_modem_abort();
    fwk_post(ev_tx_burst(0));
    uint64_t from = c->req_active ? c->t_req_us : c->t_upload_us;
    fwk_post(ev_upload_done(ok, (int32_t)((fwk_hal_now_us() - from) / 1000)));
    c->req_active = 0;
    go_idle(t, c);
}

static void go_idle(fwk_task_t *t, conn_ctx_t *c)
{
    c->state = CS_IDLE;
    if (c->req_queued) {
        c->req_queued = 0;
        begin_request(t, c);
    } else if (c->fix_pending)
        gate(c, CS_UPLOAD_GATE, 1);
    else if (c->poll_due) {
        c->poll_due = 0;
        gate(c, CS_POLL_GATE, 1);
    } else if (c->assist_pending)
        gate(c, CS_ASSIST_GATE, 1);
}

int mod_conn_task(fwk_task_t *t, const fwk_event_t *e)
{
    conn_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        c->rat = RAT_LTEM;
        c->brownouts = board_brownout_load();                     /* retained RAM: survives the supervisor reset (PWR-7, F4) */
        gate(c, CS_BOOT_GATE, 0);                                 /* boot gate: no attach until VDD_4V0 >= 3.9 V */
        break;
    case EV_RESET_CAUSE:
        if (e->a == RESET_POWERON) {
            c->brownouts = 0;                                     /* cell swap: the loop, if any, is over */
            board_brownout_save(0);
        }
        if (e->a == RESET_BROWNOUT) {                             /* F4: back off TX retries after a brown-out */
            c->brownouts++;
            board_brownout_save(c->brownouts);
            if (c->state == CS_BOOT_GATE) {
                c->state = CS_BACKOFF;
                fwk_timer_start(t, T_BACKOFF, US(backoff_s(c->brownouts - 1)), 0);
            }
        }
        break;
    case EV_TX_ALLOWED:
        if (!e->a)
            break;
        if (c->state == CS_WAIT_FIX) {
            c->gate_ok_us = fwk_hal_now_us();
            break;
        }
        if (c->state == CS_BOOT_GATE || c->state == CS_POLL_GATE)
            start_attach(t, c);
        else if (c->state == CS_UPLOAD_GATE)
            start_upload(t, c);
        else if (c->state == CS_ASSIST_GATE) {
            c->state = CS_ASSIST;
            fwk_post(ev_tx_burst(1));
            board_modem_assist_download();
            fwk_timer_start(t, T_ASSIST, US(BOARD_P_ASSIST_TIMEOUT_S), 0);
        }
        break;
    case EV_TX_POWER:
        board_modem_set_txpower(e->a);
        break;
    case EV_BATT_REPORT:
        c->idle_mv = e->a;
        c->loaded_mv = e->b;
        break;
    case EV_LOW_BATT:
        c->low_batt = 1;
        break;
    case EV_ASSIST_REQ:
        c->assist_pending = 1;
        if (c->state == CS_IDLE)
            go_idle(t, c);
        break;
    case EV_FIX_RESULT:
        c->fix_valid = e->a;
        c->fix_age = e->b;
        if (c->state == CS_WAIT_FIX && c->gate_ok_us && fwk_hal_now_us() - c->gate_ok_us < US(10)) {
            start_upload(t, c);                                   /* gate passed during the fix, GNSS alone cannot drain the supercap */
        } else if (c->state == CS_WAIT_FIX || c->state == CS_IDLE) {
            gate(c, CS_UPLOAD_GATE, 1);                           /* between bursts: check again before TX */
            if (c->req_active) {
                uint64_t used = fwk_hal_now_us() - c->t_req_us;
                fwk_timer_start(t, T_DEADLINE, used < REQ_BUDGET_US ? REQ_BUDGET_US - used : 1, 0);
            }
        } else
            c->fix_pending = 1;                                   /* a lost-mode fix while busy: upload when idle */
        break;
    case EV_MODEM_EVT:
        switch (e->a) {
        case MK_ATTACH_OK:
            if (c->state != CS_ATTACH)
                break;
            fwk_timer_stop(t, T_ATTACH);
            fwk_post(ev_tx_burst(0));
            c->tried_nb = 0;
            if (c->brownouts) {                                   /* a good attach ends the brown-out backoff */
                c->brownouts = 0;
                board_brownout_save(0);
            }
            c->mode = e->b ? 1 : 2;
            board_modem_sleep_mode(e->b ? SLEEP_EDRX : SLEEP_POLL);
            fwk_post(ev_link_state(c->mode, c->rat));
            if (c->mode == 2)
                fwk_timer_start(t, T_POLL, US(BOARD_P_POLL_PERIOD_MIN * 60), 0);
            else
                fwk_timer_stop(t, T_POLL);
            go_idle(t, c);
            break;
        case MK_ATTACH_FAIL:
            if (c->state == CS_ATTACH)
                attach_failed(t, c);
            break;
        case MK_UPLOAD_OK:
        case MK_UPLOAD_FAIL:
            if (c->state == CS_UPLOAD)
                finish_upload(t, c, e->a == MK_UPLOAD_OK);
            break;
        case MK_ASSIST_OK:
        case MK_ASSIST_FAIL:
            if (c->state == CS_ASSIST) {
                fwk_timer_stop(t, T_ASSIST);
                fwk_post(ev_tx_burst(0));
                c->assist_pending = 0;
                if (e->a == MK_ASSIST_OK)
                    board_assist_store(e->b);                         /* EPO data kept for the next power-up of the LC76G */
                fwk_post(ev_assist_done(e->a == MK_ASSIST_OK, e->b));
                go_idle(t, c);
            }
            break;
        case MK_REQ_POSITION:
        case MK_POLL_REPLY:
            if (e->a == MK_POLL_REPLY && !e->b)
                break;
            if (c->state == CS_ASSIST) {                           /* a request outranks the assistance download (IF-2) */
                fwk_timer_stop(t, T_ASSIST);
                board_modem_abort();
                fwk_post(ev_tx_burst(0));
                c->assist_pending = 0;
                fwk_post(ev_assist_done(0, 0));
                begin_request(t, c);
            } else if (c->state == CS_IDLE)
                begin_request(t, c);
            else if (c->state != CS_WAIT_FIX && c->state != CS_UPLOAD_GATE && c->state != CS_UPLOAD)
                c->req_queued = 1;                                 /* attach or assistance running: answer right after */
            break;
        case MK_REQ_BUZZER:
            fwk_post(ev_buzz_cmd(e->b));
            break;
        case MK_LOST_START:
            fwk_post(ev_lost_cmd(1));
            break;
        case MK_LOST_STOP:
            fwk_post(ev_lost_cmd(0));
            break;
        }
        break;
    case FWK_EV_TIMER:
        switch (e->a) {
        case T_ATTACH:
            if (c->state == CS_ATTACH)
                attach_failed(t, c);
            break;
        case T_UPLOAD:
            if (c->state == CS_UPLOAD)
                finish_upload(t, c, 0);                            /* the 10 s cap is part of the 125 s allowance */
            break;
        case T_ASSIST:
            if (c->state == CS_ASSIST) {
                board_modem_abort();
                fwk_post(ev_tx_burst(0));
                c->assist_pending = 0;
                fwk_post(ev_assist_done(0, 0));
                go_idle(t, c);
            }
            break;
        case T_POLL:
            c->tried_nb = 0;
            if (c->state == CS_IDLE)
                gate(c, CS_POLL_GATE, 1);
            else
                c->poll_due = 1;
            break;
        case T_DEADLINE:                                           /* gate still closed at the end of the budget: no TX */
            if (c->state == CS_UPLOAD_GATE && c->req_active) {
                fwk_post(ev_upload_done(0, (int32_t)((fwk_hal_now_us() - c->t_req_us) / 1000)));
                c->req_active = 0;
                go_idle(t, c);
            }
            break;
        case T_BACKOFF:
            gate(c, CS_BOOT_GATE, 0);
            break;
        }
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
