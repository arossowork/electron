/* Pure helpers used by the modules (no HAL, no events): NTC conversion (R17, R19) and the battery rules (R23, R35). */
#ifndef THERMO_LIB_H
#define THERMO_LIB_H
#include <stdint.h>

enum { NTC_OK = 0, NTC_OPEN = 1, NTC_SHORT = 2, NTC_NO_REF = 3 };
int     ntc_ratio_q16(int32_t code_ain, int32_t code_ref, uint32_t *ratio_q16);   /* returns NTC_* */
int32_t ntc_temp_mc(uint32_t ratio_q16);

typedef struct {
    uint8_t low_count, cutoff, warned, has_last, not_alkaline;
    int16_t last_on_mv;
} bat_state_t;
int bat_guard_wanted(const bat_state_t *b, int s1_mv);   /* rule 8a with the P6 latch */
int bat_guard_rule8a(const bat_state_t *b, int s1_mv);   /* rule 8a as first written (kept for the P6 test) */
void bat_store(bat_state_t *b, int guard_on, int mv);
enum { BAT_OK = 0, BAT_WARN, BAT_CUTOFF_NOW, BAT_CUT };
int bat_update(bat_state_t *b, int mv);

#endif
