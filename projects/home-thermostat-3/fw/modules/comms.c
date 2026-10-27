/* comms: Wi-Fi/MQTT on the port (here: counters only), default set-points, setup button and LED (R32). */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"

enum { T_LED = 1 };

int mod_comms_task(fwk_task_t *t, const fwk_event_t *e)
{
    comms_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        for (int r = 0; r < NROOM; r++)
            fwk_post(ev_setpoint(r, 20000));        /* until the schedule arrives over MQTT (R21: stored in NVS) */
        break;
    case EV_VBAT:        c->vbat_mv = e->a; break;
    case EV_BAT_WARN:    c->warn++; break;
    case EV_BAT_CUTOFF:  c->cutoff++; break;          /* port: publish "battery empty" (R23) */
    case EV_REVERSE_FAULT:
    case EV_PROBE_FAULT: c->faults++; break;
    case EV_TEMP:        c->temps++; break;
    case EV_RELAY_DEFERRED: c->deferred++; break;
    case EV_RELAY_STATE: break;
    case EV_BUTTON:                                   /* port: start Wi-Fi provisioning */
        c->buttons++;
        board_gpio_out(BOARD_GPIO_LED_N, BOARD_LED_N_ACTIVE);
        fwk_timer_start(t, T_LED, (uint64_t)BOARD_LED_PULSE_MAX_MS * 1000u, 0);
        break;
    case FWK_EV_TIMER:
        if (e->a == T_LED)
            board_gpio_out(BOARD_GPIO_LED_N, !BOARD_LED_N_ACTIVE);
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
