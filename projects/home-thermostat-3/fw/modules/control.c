/* control: on/off with hysteresis per room (R21); a probe fault or the cut-off opens the relay (F5, F6). Plain event
 * handler: it never waits, so it needs no coroutine. */
#include "board.h"
#include "ctx.h"
#include "modules.h"

#ifndef APP_HYST_MC
#define APP_HYST_MC 400      /* product parameter without a requirement (partition.md Q4) */
#endif
enum { CMD_CLOSE = 1, CMD_FORCE = 2 };

int mod_control_task(fwk_task_t *t, const fwk_event_t *e)
{
    control_ctx_t *c = t->ctx;
    int r = (int)e->a;
    switch (e->type) {
    case EV_SETPOINT:
        if (r >= 0 && r < NROOM)
            c->setpoint_mc[r] = e->b;
        break;
    case EV_RELAY_STATE:
        if (r >= 0 && r < NROOM)
            c->closed[r] = (uint8_t)e->b;
        break;
    case EV_TEMP:
        if (c->cutoff || r < 0 || r >= NROOM)
            break;
        if (e->b < c->setpoint_mc[r] - APP_HYST_MC / 2 && !c->closed[r])
            fwk_post(ev_relay_cmd(r, CMD_CLOSE));
        else if (e->b > c->setpoint_mc[r] + APP_HYST_MC / 2 && c->closed[r])
            fwk_post(ev_relay_cmd(r, 0));
        break;
    case EV_PROBE_FAULT:
        if (!c->cutoff && r >= 0 && r < NROOM && c->closed[r])
            fwk_post(ev_relay_cmd(r, 0));
        break;
    case EV_BAT_CUTOFF:
        c->cutoff = 1;
        for (int i = 0; i < NROOM; i++)
            fwk_post(ev_relay_cmd(i, CMD_FORCE));
        break;
    default:
        break;
    }
    (void)t;
    return FWK_WAIT;
}
