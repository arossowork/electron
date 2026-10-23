/* Room control (R21): on/off with hysteresis and a minimum dwell between relay changes.
 * The hysteresis has no requirement yet (partition.md Q4); the dwell comes from the relay pulse budget in board.h. */
#ifndef CONTROL_H
#define CONTROL_H
#include <stdint.h>
#include "ntc.h"

#ifndef APP_HYST_MC
#define APP_HYST_MC 400          /* 0.4 C band: product parameter, no requirement (partition.md Q4) */
#endif

typedef struct {
    int32_t  setpoint_mc;
    uint8_t  on;                 /* commanded state */
    uint8_t  changed;            /* at least one change since boot */
    uint32_t last_change_s;
} room_t;

/* returns the wanted relay state; a probe fault turns the room OFF at once (F6, R19) */
int control_decide(room_t *r, int32_t temp_mc, ntc_status_t st, uint32_t now_s, uint32_t min_dwell_s);

#endif
