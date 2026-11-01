/* module wdt: TPL5010 DONE on each WAKE, reset cause log. emits: RESET_CAUSE; owns: WDT_DONE, WDT_WAKE.
 * The external watchdog ends every output that can hurt when SW stops: a hung MCU misses DONE and the TPL5010 resets it
 * (buzzer PWM, GNSS_EN and the LED default off after reset). */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"

int mod_wdt_task(fwk_task_t *t, const fwk_event_t *e)
{
    wdt_ctx_t *c = t->ctx;
    switch (e->type) {
    case FWK_EV_INIT:
        fwk_post(ev_reset_cause(board_reset_cause()));
        break;
    case EV_WDT_WAKE_IRQ:
        /* the scheduler delivered the event: queue and dispatcher are alive. A module that blocks starves this event. */
        board_wdt_done_pulse();
        c->dones++;
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
