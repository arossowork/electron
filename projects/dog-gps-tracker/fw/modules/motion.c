/* module motion: LIS2DW12 wake-on-motion (IF-9, could). emits: MOTION; owns: ACCEL_INT, ACCEL_SDA, ACCEL_SCL. */
#include "board.h"
#include "board_hal.h"
#include "ctx.h"
#include "modules.h"
#include "events.h"

enum { T_REARM = 1 };

int mod_motion_task(fwk_task_t *t, const fwk_event_t *e)
{
    switch (e->type) {
    case FWK_EV_INIT:
        board_accel_arm();
        break;
    case EV_ACCEL_IRQ:
        fwk_post(ev_motion(0));
        fwk_timer_start(t, T_REARM, 1000000ull, 0);          /* lock out for 1 s, then clear the latch and re-arm */
        break;
    case FWK_EV_TIMER:
        if (e->a == T_REARM)
            board_accel_arm();
        break;
    default:
        break;
    }
    return FWK_WAIT;
}
