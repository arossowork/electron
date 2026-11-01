/* module power: boot gate, battery sampling, TX power back-off, low-battery report.
 * emits: TX_ALLOWED, TX_POWER, BATT_REPORT, LOW_BATT; owns: VBAT_SENSE_EN, VBAT_SENSE. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"

enum { T_RETRY = 1, T_HOURLY, T_LOADED, T_IDLE };
#define US(s) ((uint64_t)(s) * 1000000ull)
#define MS(m) ((uint64_t)(m) * 1000ull)

static void decide_power(power_ctx_t *c)
{
    int loaded = c->loaded_mv > 0 ? c->loaded_mv : 5000;     /* no loaded sample yet: judge on temperature only */
    int dbm = tx_power_decide(loaded, board_temp_c10(), c->dbm ? c->dbm : BOARD_P_TX_NOMINAL_DBM);
    if (c->brownout_seen && dbm == BOARD_P_TX_NOMINAL_DBM && c->loaded_mv == 0)
        dbm = BOARD_P_TX_BACKOFF_DBM;                          /* after a brown-out stay backed off until a loaded sample */
    if (dbm != c->dbm) {
        c->dbm = dbm;
        fwk_post(ev_tx_power(dbm));
    }
}

static void gate_check(fwk_task_t *t, power_ctx_t *c)
{
    int mv = board_modem_vdd_mv();
    decide_power(c);
    if (tx_gate_ok(mv)) {                                      /* PWR-7: attach/TX only from here */
        c->pending = 0;
        c->tries = 0;
        fwk_post(ev_tx_allowed(1, mv));
        return;
    }
    if (c->tries == 0)
        fwk_post(ev_tx_allowed(0, mv));
    c->pending = 1;
    /* supercap recharge takes 2.7 s worst case: poll every 3 s ten times, then once a minute (a dead cell must not spin) */
    fwk_timer_start(t, T_RETRY, US(c->tries < 10 ? BOARD_P_GATE_RETRY_S : 60), 0);
    c->tries++;
}

/* VBAT_SENSE_EN: on only while a sample is pending (PWR-5); two overlapping samples share the divider */
static void sample(fwk_task_t *t, power_ctx_t *c, int id, int delay_ms)
{
    c->pend |= 1 << id;
    board_vbat_divider(1);
    fwk_timer_start(t, id, MS(delay_ms), 0);
}

static void sample_done(power_ctx_t *c, int id)
{
    c->pend &= ~(1 << id);
    if (!c->pend)
        board_vbat_divider(0);
}

int mod_power_task(fwk_task_t *t, const fwk_event_t *e)
{
    power_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        board_vbat_divider(0);
        fwk_timer_start(t, T_HOURLY, US(BOARD_P_BATT_PERIOD_MIN * 60), US(BOARD_P_BATT_PERIOD_MIN * 60));
        break;
    case EV_BATT_CHECK:
        gate_check(t, c);
        break;
    case EV_RESET_CAUSE:
        if (e->a == RESET_BROWNOUT) {
            c->brownout_seen = 1;
            c->force_low = 1;                                  /* F4: report low battery after a brown-out reset */
            decide_power(c);
            sample(t, c, T_IDLE, BOARD_P_VBAT_SETTLE_MS);
        }
        break;
    case EV_TX_BURST:
        if (e->a)
            sample(t, c, T_LOADED, BOARD_P_TX_SAMPLE_DELAY_MS);   /* F7: a defined point of the load cycle */
        else {
            fwk_timer_stop(t, T_LOADED);
            sample_done(c, T_LOADED);
            sample(t, c, T_IDLE, BOARD_P_VBAT_SETTLE_MS);
        }
        break;
    case FWK_EV_TIMER:
        if (e->a == T_RETRY)
            gate_check(t, c);
        else if (e->a == T_LOADED) {
            c->loaded_mv = batt_cell_mv(board_vbat_pin_mv());
            sample_done(c, T_LOADED);
            /* C16: a sag counts only above tx_cold_limit_c (cold alkaline sags by design, PWR-7); consecutive samples */
            if (c->loaded_mv < BOARD_P_TX_CELL_SAG_MV && board_temp_c10() >= BOARD_P_TX_COLD_LIMIT_C * 10)
                c->sags++;
            else
                c->sags = 0;
        } else if (e->a == T_IDLE) {
            c->idle_mv = batt_cell_mv(board_vbat_pin_mv());
            sample_done(c, T_IDLE);
            fwk_post(ev_batt_report(c->idle_mv, c->loaded_mv));
            decide_power(c);
            if (low_batt_decide(c->idle_mv, c->sags) || c->force_low)
                fwk_post(ev_low_batt(c->idle_mv));
            c->force_low = 0;
        } else if (e->a == T_HOURLY)
            sample(t, c, T_IDLE, BOARD_P_VBAT_SETTLE_MS);
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
