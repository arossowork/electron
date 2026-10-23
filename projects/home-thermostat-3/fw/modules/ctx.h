/* Module contexts: each module's private state, allocated by app.c and reached only through its own task
 * (t->ctx). Kept in RTC memory on the target so they survive deep sleep. */
#ifndef CTX_H
#define CTX_H
#include <stdint.h>
#include "thermo_lib.h"

enum { RES_SENS = 1, RES_GUARD = 2 };   /* EV_HOLD resource bits (interface.yaml events.HOLD) */
#define NROOM 2

typedef struct {
    bat_state_t bat;
    uint8_t sens_cnt, guard_cnt, guard_on, tick, guard_req;
    int s1, s2, mv;
} power_ctx_t;

typedef struct {
    int16_t ref, code[NROOM];
    uint8_t ch, holding;
} sensors_ctx_t;

typedef struct {
    int32_t setpoint_mc[NROOM];
    uint8_t closed[NROOM], cutoff;
} control_ctx_t;

typedef struct {
    int8_t   want[NROOM];          /* -1 none, 0 open, 1 close */
    uint8_t  force[NROOM], closed[NROOM], switched[NROOM], ready, active;
    uint64_t last_switch_us[NROOM];
} actuators_ctx_t;

typedef struct {
    int vbat_mv, warn, cutoff, faults, temps, deferred, buttons;
} comms_ctx_t;

#endif
