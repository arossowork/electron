/* Simulated board for host tests: cells and reverse guard (rule 8 timing), VBAT divider, ADS1115 with two NTC
 * probes, two DRV8837 + latching relays. Numbers from notes/circuit.md; the electrical truth stays in SPICE. */
#ifndef SIM_HAL_H
#define SIM_HAL_H
#include <stdint.h>

#define SIM_GPIO 22
#define SIM_MAX_SEG 64

enum { SEG_COAST = 0, SEG_FWD, SEG_REV, SEG_BRAKE };

typedef struct { int relay, mode, sens; uint64_t t0, t1; } sim_seg_t;

typedef struct {
    uint64_t now;
    int level[SIM_GPIO], is_out[SIM_GPIO];
    /* cells */
    int vbat_raw_mv;      /* loaded cell voltage */
    int vf_mv;            /* guard body-diode drop with the guard OFF (0.39-0.51 V, notes/circuit.md) */
    int rise50_mv;        /* VBAT_P rise in the first 50 us after IO2 low (lithium at 0 C: 3.19 -> 3.26 V) */
    int reversed;         /* cells reversed: C1+C2 dump, about 0.1 V per 50 us (rule 8b) */
    /* probes */
    double temp_c[2];
    int ntc_mode[2];      /* 0 ok, 1 open, 2 short */
    int wifi_busy;
    /* observations */
    uint64_t io2_low_t0, io2_low_max_us;
    int io2_low;
    int wdt_feeds, i2c_unpowered, adc_bad_channel;
    int contact[2];
    sim_seg_t seg[SIM_MAX_SEG];
    int nseg, open_seg[2];
    /* ADS1115 */
    uint16_t ads_cfg;
    uint8_t ads_ptr;
    uint64_t ads_done;
} sim_t;

extern sim_t sim;
void sim_reset(void);
int  sim_lithium(void);   /* cells that lift +3V3 above R35 if M4 conducts (>= 3.5 V) */

#endif
