/* One control wake (R21, every BOARD_CONTROL_PERIOD_S): battery and guard sequence (rule 8), sensors (R17, R19),
 * cut-off (R23), room control and relay pulses (R9). Wi-Fi and MQTT are outside the core (port). */
#ifndef APP_H
#define APP_H
#include <stdint.h>
#include "battery.h"
#include "control.h"
#include "ntc.h"
#include "relay.h"

typedef struct {               /* RTC memory: survives deep sleep, lost on a cold boot */
    bat_state_t bat;
    relays_t    rl;
    room_t      room[RELAY_COUNT];
} app_rtc_t;

typedef struct {
    int32_t      temp_mc[RELAY_COUNT];
    ntc_status_t st[RELAY_COUNT];
    int          vbat_mv;
    int          guard_on;
    bat_event_t  bat;
    int          fault_reverse;    /* F3: report over MQTT */
    int          sensor_fault;     /* ADS1115 not answering */
    uint32_t     sens_on_us;       /* SENS_EN high time (energy, partition.md P4) */
} wake_report_t;

/* cold boot: clear RTC state, then re-assert both relay states from NVS (rule 6) */
void app_cold_boot(app_rtc_t *s, const uint8_t stored_state[RELAY_COUNT]);
void app_control_wake(app_rtc_t *s, uint32_t now_s, wake_report_t *out);

#endif
