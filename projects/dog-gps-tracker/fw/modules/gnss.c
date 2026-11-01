/* module gnss: LC76G power gating, fix with a timeout, assistance age.
 * emits: FIX_RESULT, ASSIST_REQ; owns: GNSS_EN, GNSS_RX, GNSS_TX, GNSS_RST_N.
 * Back-power rule: while GNSS_EN is low, GNSS_RX is driven low (or disconnected) and GNSS_RST_N is Hi-Z, never high.
 * Power-up: EN, wait gnss_boot_ms, then connect the UART; power-down: disconnect the UART first, then EN. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"
#include "fwk_hal.h"

enum { T_BOOT = 1, T_FIX, T_AGE, T_HOLD };
#define US(s) ((uint64_t)(s) * 1000000ull)

static void safe_pins(void)
{
    board_gnss_uart(0);       /* GNSS_RX low, GNSS_TX input without pull */
    board_gnss_rst(0);        /* Hi-Z */
}

static void finish(fwk_task_t *t, gnss_ctx_t *c, int valid)
{
    fwk_timer_stop(t, T_BOOT);
    fwk_timer_stop(t, T_FIX);
    safe_pins();                                  /* before EN goes low */
    board_gnss_en(0);
    fwk_stay_awake(t, 0);
    c->phase = 0;
    if (valid) {
        c->has_fix = 1;
        c->t_last_fix_us = fwk_hal_now_us();
    }
    int age = valid ? 0 : (c->has_fix ? (int)((fwk_hal_now_us() - c->t_last_fix_us) / 1000000ull) : -1);
    fwk_post(ev_fix_result(valid, age));          /* F5: valid 0 = last known position with its age, never a longer wait */
}

static void check_age(fwk_task_t *t, gnss_ctx_t *c)
{
    (void)t;
    int age = board_assist_age_h();
    uint64_t now = fwk_hal_now_us();
    int gap_ok = !c->assist_asked || now - c->t_assist_ask_us >= US(BOARD_P_ASSIST_RETRY_MIN * 60);
    if (assist_due(age) && gap_ok) {           /* assist_period_h = validity - 12 h lead: refreshed before it expires (IF-3) */
        c->assist_asked = 1;
        c->t_assist_ask_us = now;
        fwk_post(ev_assist_req(age));
    }
}

static void start_fix(fwk_task_t *t, gnss_ctx_t *c)
{
    c->phase = 1;
    c->hold = 0;
    fwk_timer_stop(t, T_HOLD);
    fwk_stay_awake(t, 1);                       /* UART RX must run while the receiver is on */
    board_gnss_en(1);
    fwk_timer_start(t, T_BOOT, (uint64_t)BOARD_P_GNSS_BOOT_MS * 1000ull, 0);
    fwk_timer_start(t, T_FIX, US(BOARD_P_GNSS_FIX_TIMEOUT_S), 0);   /* 30 s from the request, boot included */
}

int mod_gnss_task(fwk_task_t *t, const fwk_event_t *e)
{
    gnss_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        board_gnss_en(0);
        safe_pins();
        fwk_timer_start(t, T_AGE, US(3600), US(3600));
        check_age(t, c);                            /* asks at power-up; conn downloads after the first attach */
        break;
    case EV_POS_REQUEST:
        if (c->hold && e->a == 0) {                 /* a network request outranks the wait for the download (IF-2) */
            start_fix(t, c);
            break;
        }
        if (c->phase || c->hold)
            break;                                  /* a fix is running: its result serves this request too */
        if (e->a == 1 && c->lost_asked) {           /* first lost-mode fix waits for the assistance download (C14) */
            c->hold = 1;
            c->lost_asked = 0;
            fwk_timer_start(t, T_HOLD, US(BOARD_P_ASSIST_TIMEOUT_S), 0);
            break;
        }
        start_fix(t, c);
        break;
    case EV_LOST_STATE:
        /* C14: a lost session starts with a file that does not cover its 2 h, and 120 fixes save more than one download costs */
        if (e->a == 1 && !assist_covers(board_assist_age_h(), BOARD_P_LOST_MAX_DURATION_S / 3600) &&
            assist_pays_off(BOARD_LOST_SESSION_FIXES)) {
            c->assist_asked = 1;
            c->t_assist_ask_us = fwk_hal_now_us();
            c->lost_asked = 1;
            fwk_post(ev_assist_req(board_assist_age_h()));
        }
        break;
    case FWK_EV_TIMER:
        if (e->a == T_HOLD && c->hold)
            start_fix(t, c);                        /* download did not finish in assist_timeout_s: cold fix */
        else if (e->a == T_BOOT && c->phase == 1) {
            board_gnss_rst(0);                      /* released: Hi-Z, the receiver's pull-up holds RST_N high */
            board_gnss_uart(1);
            board_gnss_inject_assist();             /* EPO data stored by the last download */
            c->phase = 2;
        } else if (e->a == T_FIX && c->phase)
            finish(t, c, 0);
        else if (e->a == T_AGE)
            check_age(t, c);
        break;
    case EV_GNSS_EVT:
        if (c->phase == 2 && e->a == GK_FIX_OK)
            finish(t, c, 1);
        break;
    case EV_ASSIST_DONE:
        /* the retry gap in check_age holds; a failed download is asked again after assist_retry_min */
        if (c->hold)
            start_fix(t, c);                        /* done or failed: the waiting lost-mode fix starts now */
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
