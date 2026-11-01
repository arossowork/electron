/* module fota: chunked FOTA download with resume, yields to requests, battery and supercap gate, image confirm or rollback (IF-8, C4).
 * emits: FOTA_STATE, BATT_CHECK. Owns no pin. Scheme: app image in the MCUboot secondary slot (test upgrade, then confirm,
 * else MCUboot reverts at the next reset), modem delta through the modem library; the server offers an image with
 * MODEM_EVT kind 12 (b = kB). Each chunk passes the same supercap gate as a TX burst; a locate request (any source) or an
 * assistance download aborts the chunk in flight and the download resumes at the stored offset afterwards (IF-2 priority). */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"

enum { T_RESUME = 1, T_CONFIRM, T_CHUNK, T_NEXT };
#define MS(m) ((uint64_t)(m) * 1000ull)
#define US(s) ((uint64_t)(s) * 1000000ull)

static void state(fota_ctx_t *c, int s)
{
    c->state = s;
    uint32_t total = c->total_kb * 1024u;
    int pct = total ? (int)((uint64_t)c->offset * 100u / total) : 0;
    int code = s == FS_DOWNLOAD || s == FS_WAIT_GATE ? 1 : s == FS_YIELD || s == FS_PAUSED_LOW ? 2 : s == FS_APPLIED ? 3 : 0;
    fwk_post(ev_fota_state(code, pct));
}

static void stop_chunk(fwk_task_t *t, fota_ctx_t *c)
{
    fwk_timer_stop(t, T_CHUNK);
    if (c->state == FS_DOWNLOAD) {
        c->chunk_id++;                       /* any late completion is now stale */
        board_fota_abort();
        board_modem_abort();
        fwk_post(ev_tx_burst(0));
    }
}

static void try_start(fwk_task_t *t, fota_ctx_t *c)
{
    if (!c->total_kb || c->state == FS_APPLIED || c->state == FS_WAIT_GATE || c->state == FS_DOWNLOAD)
        return;
    if (c->low_batt) {                       /* no start below the low-battery level (IF-8, C16) */
        state(c, FS_PAUSED_LOW);
        return;
    }
    if (c->busy || c->link == 0) {           /* a request or assistance runs, or no link: wait for it to end */
        if (c->state != FS_YIELD)
            state(c, FS_YIELD);
        fwk_timer_start(t, T_RESUME, US(BOARD_P_FOTA_RESUME_S), 0);
        return;
    }
    fwk_timer_stop(t, T_RESUME);
    state(c, FS_WAIT_GATE);
    fwk_post(ev_batt_check(2));              /* supercap gate (attach_min_vbat_mv) before every chunk */
}

static void yield(fwk_task_t *t, fota_ctx_t *c)
{
    if (c->state != FS_DOWNLOAD && c->state != FS_WAIT_GATE)
        return;
    stop_chunk(t, c);
    state(c, FS_YIELD);
    fwk_timer_start(t, T_RESUME, US(BOARD_P_FOTA_RESUME_S), 0);
}

int mod_fota_task(fwk_task_t *t, const fwk_event_t *e)
{
    fota_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        board_fota_progress_load(&c->offset, &c->total_kb);
        if (board_fota_pending_confirm()) {  /* first boot of a test image: confirm after an attach, else let MCUboot revert it */
            c->pending_confirm = 1;
            fwk_timer_start(t, T_CONFIRM, US(BOARD_P_FOTA_CONFIRM_S), 0);
        }
        break;
    case EV_LINK_STATE:
        c->link = e->a;
        if (e->a && c->pending_confirm) {
            c->pending_confirm = 0;
            fwk_timer_stop(t, T_CONFIRM);
            board_fota_confirm();
            fwk_post(ev_fota_state(4, 100));
        }
        if (e->a && c->state == FS_YIELD)
            try_start(t, c);
        break;
    case EV_MODEM_EVT:
        if (e->a == MK_FOTA_OFFER) {
            if (c->state != FS_IDLE)
                break;
            if (!fota_size_ok(e->b)) {       /* does not fit the slot: refuse, stay idle */
                fwk_post(ev_fota_state(6, 0));
                break;
            }
            if (c->total_kb != (uint32_t)e->b)
                c->offset = 0;               /* a different image: no resume */
            c->total_kb = (uint32_t)e->b;
            board_fota_progress_save(c->offset, c->total_kb);
            try_start(t, c);
        } else if ((e->a == MK_FOTA_CHUNK || e->a == MK_FOTA_FAIL) && ((uint32_t)e->b >> 16) != (c->chunk_id & 0xFFFF)) {
            break;                           /* completion of a chunk that was aborted */
        } else if (e->a == MK_FOTA_CHUNK && c->state == FS_DOWNLOAD) {
            fwk_timer_stop(t, T_CHUNK);
            fwk_post(ev_tx_burst(0));
            c->offset += (uint32_t)e->b & 0xFFFF;
            c->fails = 0;
            board_fota_progress_save(c->offset, c->total_kb);
            if (!fota_next_len(c->offset, c->total_kb * 1024u)) {
                board_fota_apply();          /* image complete: MCUboot test upgrade at the next boot */
                state(c, FS_APPLIED);
            } else {
                c->state = FS_YIELD;         /* next chunk after the idle VBAT sample (2 x vbat_settle_ms), so LOW_BATT can stop it */
                fwk_post(ev_fota_state(1, (int)((uint64_t)c->offset * 100u / (c->total_kb * 1024u))));
                fwk_timer_start(t, T_NEXT, MS(2 * BOARD_P_VBAT_SETTLE_MS), 0);
            }
        } else if (e->a == MK_FOTA_FAIL && c->state == FS_DOWNLOAD) {
            fwk_timer_stop(t, T_CHUNK);
            fwk_post(ev_tx_burst(0));
            if (++c->fails > 5) {            /* give up: the server offers again on the next contact */
                c->fails = 0;
                state(c, FS_IDLE);
                c->total_kb = 0;
                board_fota_progress_save(0, 0);
            } else {
                state(c, FS_YIELD);
                fwk_timer_start(t, T_RESUME, US(BOARD_P_FOTA_RESUME_S), 0);
            }
        }
        break;
    case EV_TX_ALLOWED:
        if (c->state != FS_WAIT_GATE || !e->a)
            break;                           /* gate closed: power asks again until the supercap is back */
        {
            uint32_t len = fota_next_len(c->offset, c->total_kb * 1024u);
            c->state = FS_DOWNLOAD;
            fwk_post(ev_tx_burst(1));
            board_fota_fetch(c->offset, len, ++c->chunk_id);
            fwk_timer_start(t, T_CHUNK, US(BOARD_P_ASSIST_TIMEOUT_S), 0);
        }
        break;
    case EV_LOW_BATT:
        c->low_batt = 1;
        if (c->state == FS_DOWNLOAD || c->state == FS_WAIT_GATE) {
            stop_chunk(t, c);
            state(c, FS_PAUSED_LOW);
        } else if (c->state == FS_YIELD) {
            fwk_timer_stop(t, T_RESUME);
            state(c, FS_PAUSED_LOW);
        }
        break;
    case EV_BATT_REPORT:
        if (c->low_batt && low_batt_clear(e->a)) {
            c->low_batt = 0;
            if (c->state == FS_PAUSED_LOW)
                try_start(t, c);
        }
        break;
    case EV_POS_REQUEST:
    case EV_ASSIST_REQ:
        c->busy = 1;
        yield(t, c);
        break;
    case EV_UPLOAD_DONE:
    case EV_ASSIST_DONE:
        c->busy = 0;
        if (c->state == FS_YIELD)
            try_start(t, c);
        break;
    case FWK_EV_TIMER:
        if (e->a == T_NEXT) {
            if (c->state == FS_YIELD)
                try_start(t, c);
        } else if (e->a == T_RESUME) {
            c->busy = 0;                     /* bound: a request that never closes cannot hold the download */
            if (c->state == FS_YIELD)
                try_start(t, c);
        } else if (e->a == T_CHUNK && c->state == FS_DOWNLOAD) {
            stop_chunk(t, c);
            state(c, FS_YIELD);
            fwk_timer_start(t, T_RESUME, US(BOARD_P_FOTA_RESUME_S), 0);
        } else if (e->a == T_CONFIRM && c->pending_confirm) {
            c->pending_confirm = 0;
            fwk_post(ev_fota_state(5, 0));
            board_fota_revert_reboot();      /* not confirmed: MCUboot swaps the previous image back */
        }
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
