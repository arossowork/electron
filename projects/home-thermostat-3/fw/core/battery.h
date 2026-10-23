/* Battery logic: cut-off (R23), low warning (R22), guard decision (R35, rule 8a), reverse check (R4, rule 8b).
 * Pure functions on millivolts; the wake sequence that takes the readings is in app.c. */
#ifndef BATTERY_H
#define BATTERY_H
#include <stdint.h>

typedef enum { BAT_OK = 0, BAT_WARN, BAT_CUTOFF_NOW, BAT_CUTOFF } bat_event_t;

typedef struct {               /* kept in RTC memory across deep sleep; cleared by a cold boot (new cells) */
    uint8_t low_count;
    uint8_t cutoff;            /* latched: no relay activity after the cut-off */
    uint8_t warned;
    uint8_t has_last;          /* a stored guard decision reading exists */
    uint8_t lithium;           /* partition.md P6: chemistry latched by a guard-OFF decision */
    int16_t last_on_mv;        /* last guard-ON reading (rule 8a) */
} bat_state_t;

void        bat_cold_boot(bat_state_t *b);
int         bat_mv_from_adc(int adc_mv);                  /* VBAT_P from the divider tap */
bat_event_t bat_update(bat_state_t *b, int vbat_mv);      /* once per control wake */
int         bat_reverse_suspect(int s1_mv, int s2_mv);    /* rule 8b */
/* rule 8a as written in notes/circuit.md (kept to document partition.md P6) */
int         bat_guard_rule8a(const bat_state_t *b, int s1_mv);
/* rule 8a with the P6 fix: a guard-OFF decision (lithium) is kept while the 50 us read stays >= the cold-boot
 * threshold; only a guard-ON reading (alkaline) re-arms the 3.35 V rule */
int         bat_guard_wanted(const bat_state_t *b, int s1_mv);
void        bat_store_reading(bat_state_t *b, int guard_on, int vbat_mv);

#endif
