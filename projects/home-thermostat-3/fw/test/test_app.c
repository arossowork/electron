/* Host tests of the thermostat on the framework: simulated time, the simulated board, assertions on the events the
 * modules emit. Every number comes from board.h (generated from interface.yaml). */
#include <stdio.h>
#include "board.h"
#include "ctx.h"
#include "fwk_sim.h"
#include "modules.h"
#include "sim_board.h"

void app_init(void);
const comms_ctx_t *app_comms(void);
const actuators_ctx_t *app_actuators(void);

#define S_ (1000000ull)
#define WDT_US ((uint64_t)BOARD_WDT_TIMEOUT_MS * 1000u)

static void start(int vbat_mv, int temp_c)
{
    fwk_sim_reset(WDT_US);
    sim_board_reset(vbat_mv, temp_c);
    app_init();
}

static int relay_states(int relay, int closed)
{
    int n = 0;
    for (int i = 0; i < fsim.ntrace; i++)
        n += fsim.trace[i].e.type == EV_RELAY_STATE && fsim.trace[i].e.a == relay && fsim.trace[i].e.b == closed;
    return n;
}

static void test_day(void)
{
    start(3000, 21);                                  /* alkaline, no heating demand */
    fwk_sim_run_until(86400 * S_);
    CHECK(fwk_sim_count(EV_VBAT) == 86400 / BOARD_CONTROL_PERIOD_S, "VBAT %d", fwk_sim_count(EV_VBAT));
    CHECK(fwk_sim_count(EV_TEMP) == 2 * 86400 / BOARD_PROBE_PERIOD_S, "TEMP %d", fwk_sim_count(EV_TEMP));
    CHECK(sb.sens_on_max_us == BOARD_SENSOR_ON_US, "sensor-on %llu us, testbench %d (R6)",
          (unsigned long long)sb.sens_on_max_us, BOARD_SENSOR_ON_US);
    CHECK(sb.i2c_without_rail == 0, "I2C with the rail off");
    CHECK(fwk_dropped() == 0, "%u events dropped", fwk_dropped());
    CHECK(fsim.max_feed_gap < WDT_US && !fsim.wdt_reset, "feed gap %llu us", (unsigned long long)fsim.max_feed_gap);
    CHECK(fsim.idle_deep_us > 86300 * S_, "deep sleep %llu s", (unsigned long long)(fsim.idle_deep_us / S_));
    CHECK(sb.pulses == 0, "%d pulses", sb.pulses);
}

static void test_pulse_and_dwell(void)
{
    start(3000, 18);                                  /* below the 20 C default set-point: close */
    fwk_sim_run_until(1 * S_);
    CHECK(sb.pulses == 2 && relay_states(0, 1) == 1 && relay_states(1, 1) == 1, "pulses %d", sb.pulses);
    CHECK(sb.pulse_t[1] - sb.pulse_t[0] >= BOARD_RELAY_PULSE_US + BOARD_RELAY_BRAKE_US, "pulses overlap (rule 4)");
    CHECK(sb.coil_us_total == 2u * BOARD_RELAY_PULSE_US, "coil time %llu", (unsigned long long)sb.coil_us_total);
    CHECK(sb.plain_k_drive == 0, "bridge driven by plain GPIO");

    sb.temp_c[0] = sb.temp_c[1] = 23;                 /* above: open, but the dwell holds it (D19) */
    fwk_sim_run_until(2000 * S_);
    CHECK(fwk_sim_count(EV_RELAY_DEFERRED) > 0 && sb.pulses == 2, "deferred %d, pulses %d",
          fwk_sim_count(EV_RELAY_DEFERRED), sb.pulses);
    fwk_sim_run_until(3000 * S_);
    CHECK(relay_states(0, 0) == 1 && sb.pulses == 4, "after the dwell: pulses %d", sb.pulses);
    CHECK(sb.pulse_t[2] - sb.pulse_t[0] >= (uint64_t)BOARD_RELAY_MIN_DWELL_S * S_, "dwell %llu s",
          (unsigned long long)((sb.pulse_t[2] - sb.pulse_t[0]) / S_));
}

static void test_cutoff(void)
{
    start(1900, 18);
    fwk_sim_run_until(200 * S_);
    CHECK(fwk_sim_count(EV_BAT_CUTOFF) == 1, "cutoff %d", fwk_sim_count(EV_BAT_CUTOFF));
    const fwk_sim_rec_t *c = fwk_sim_find(EV_BAT_CUTOFF, 0);
    CHECK(c && c->t < (uint64_t)(BOARD_CUTOFF_COUNT * BOARD_CONTROL_PERIOD_S + 1) * S_, "late cutoff");
    CHECK(relay_states(0, 0) == 1 && relay_states(1, 0) == 1, "relays not forced open (R23)");
    CHECK(sb.vbat_en_low_max_us > BOARD_GUARD_RECHARGE_US + BOARD_RELAY_PULSE_US, "low alkaline: the guard does not cover the pulses (R9 at 2.0 V)");
    int p = sb.pulses;
    sb.temp_c[0] = 10;
    fwk_sim_run_until(4000 * S_);
    CHECK(sb.pulses == p, "a relay switched after the cut-off");
}

static void test_hang_during_pulse(void)
{
    start(3000, 18);
    sb.hang_in_pulse_us = 3 * 1000000;                /* the CPU stops right after starting the first pulse */
    fwk_sim_run_until(1 * S_);
    CHECK(fsim.wdt_reset, "watchdog did not fire");
    CHECK(sb.coil_us_at_hang_end == BOARD_RELAY_PULSE_US, "coil %llu us on a hang (F2)", (unsigned long long)sb.coil_us_at_hang_end);
}

static void test_lithium(void)
{
    start(3700, 18);                                  /* lithium: VBAT_EN low only for the 50 us read (rule 8d) */
    fwk_sim_run_until(3600 * S_);
    CHECK(sb.pulses >= 2, "pulses %d", sb.pulses);
    CHECK(sb.vbat_en_low_max_us < 1000, "VBAT_EN low %llu us", (unsigned long long)sb.vbat_en_low_max_us);
}

static void test_p6(void)
{
    bat_state_t b = {0};
    bat_store(&b, 0, 3300);                           /* a lithium read with the guard OFF, below 3350 mV */
    CHECK(bat_guard_rule8a(&b, 3300) == 1, "rule 8a as written does not reproduce P6");
    CHECK(bat_guard_wanted(&b, 3300) == 0, "P6 latch: the guard must stay OFF (SPICE: 3.689 V with it ON)");
    start(3300, 18);
    fwk_sim_run_until(3600 * S_);
    CHECK(sb.vbat_en_low_max_us < 1000, "P6 in the app: VBAT_EN low %llu us", (unsigned long long)sb.vbat_en_low_max_us);
}

static void test_probe_fault(void)
{
    start(3000, 18);
    fwk_sim_run_until(1 * S_);
    sb.probe_open[1] = 1;
    fwk_sim_run_until(3000 * S_);                     /* the open waits for the D19 dwell like any other */
    const fwk_sim_rec_t *f = fwk_sim_find(EV_PROBE_FAULT, 0);
    CHECK(f && f->e.a == 1 && f->e.b == NTC_OPEN, "no PROBE_FAULT(1, OPEN)");
    CHECK(relay_states(1, 0) == 1 && relay_states(0, 0) == 0, "faulty room not opened (R19)");
}

static void test_button(void)
{
    start(3000, 21);
    fwk_sim_inject_at(5 * S_ + 123, ev_button());
    fwk_sim_run_until(10 * S_);
    CHECK(app_comms()->buttons == 1, "buttons %d", app_comms()->buttons);
    CHECK(sb.led_on_us == (uint64_t)BOARD_LED_PULSE_MAX_MS * 1000u, "LED %llu us", (unsigned long long)sb.led_on_us);
}

int main(void)
{
    test_day();
    test_pulse_and_dwell();
    test_cutoff();
    test_hang_during_pulse();
    test_lithium();
    test_p6();
    test_probe_fault();
    test_button();
    printf("thermostat host tests: %d checks, %d failed\n", fwk_checks, fwk_fails);
    return fwk_fails != 0;
}
