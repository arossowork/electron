/* Simulated board for the host tests: GPIO levels with timing, the battery seen through VBAT_EN, a fake ADS1115 and
 * the peripheral-timed bridge pulse. */
#ifndef SIM_BOARD_H
#define SIM_BOARD_H
#include <stdint.h>

typedef struct {
    int      level[22], hiz[22];
    int      vbat_mv, temp_c[2], probe_open[2];
    int      plain_k_drive;                    /* board_gpio_out on a bridge input: must stay 0 (F2) */
    int      pulses, pulse_relay[16], pulse_hi[16];
    uint64_t pulse_t[16], coil_us_total;
    int      hang_in_pulse_us;                 /* > 0: the CPU hangs this long right after starting a pulse */
    uint64_t coil_us_at_hang_end;
    uint64_t sens_on_t, sens_on_last_us, sens_on_max_us;
    uint64_t vbat_en_low_t, vbat_en_low_max_us;
    uint64_t led_on_t, led_on_us;
    int      i2c_without_rail;
} sim_board_t;

extern sim_board_t sb;
void sim_board_reset(int vbat_mv, int temp_c);

#endif
