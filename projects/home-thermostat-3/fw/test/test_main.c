/* Host tests: the firmware core against board.h (generated from design.yaml + interface.yaml) and the
 * requirements. `make test` regenerates board.h, builds with gcc and runs this file. */
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "app.h"
#include "board.h"
#include "hal.h"
#include "sim_hal.h"

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); \
                           printf(__VA_ARGS__); printf("\n"); } } while (0)

static app_rtc_t rtc;
static wake_report_t rep;
static const uint8_t OFF2[2] = {0, 0};

static void boot(int vbat_mv, const uint8_t stored[2])
{
    sim_reset();
    sim.vbat_raw_mv = vbat_mv;
    app_cold_boot(&rtc, stored);
    rtc.room[0].setpoint_mc = rtc.room[1].setpoint_mc = 20000;
}

static void wake(uint32_t now_s)
{
    sim.nseg = 0;
    sim.io2_low_max_us = 0;
    app_control_wake(&rtc, now_s, &rep);
}

static uint64_t seg_us(int i) { return sim.seg[i].t1 - sim.seg[i].t0; }

/* R9, R22, R23: the constants themselves against the requirement numbers */
static void test_board_constants(void)
{
    CHECK(BOARD_RELAY_PULSE_US >= 2 * 15000, "R9: pulse >= 2 x 15 ms set time");
    double e_mj = BOARD_RELAY_RAIL_MAX_V * BOARD_RELAY_RAIL_MAX_V / BOARD_RELAY_COIL_MIN_OHM * BOARD_RELAY_PULSE_US * 1e-3;
    CHECK(e_mj <= 9.0, "R9: worst pulse energy %.2f mJ <= 9 mJ", e_mj);
    CHECK(BOARD_VBAT_CUTOFF_MV == 2000, "R23: cut-off 2.0 V");
    double err = 10.0 * BOARD_VBAT_DIV_RATIO + 3400.0 * BOARD_VBAT_DIV_TOL;
    CHECK(err <= BOARD_VBAT_ERR_MV, "R22: ADC 10 mV x ratio + divider tolerance = %.1f mV <= 50", err);
    CHECK(BOARD_I2C_ADDR_ADS == 0x48, "U3 ADDR on GND");
    printf("  R9 worst pulse energy %.2f mJ (limit 9), R22 VBAT error %.1f mV (limit 50)\n", e_mj, err);
}

/* R17: table + quantization error against the Beta model, and resolution */
static void test_ntc_accuracy(void)
{
    double worst = 0, worst_res = 0;
    const double ref = round(3300.0 / 4096.0 * 32768.0);
    for (double t = 0.0; t <= 40.0001; t += 0.05) {
        double r = 10000.0 * exp(3950.0 * (1.0 / (t + 273.15) - 1.0 / 298.15));
        double code = round(ref * r / (BOARD_NTC_RFIX_OHM + r));
        uint32_t q;
        CHECK(ntc_ratio_q16((int32_t)code, (int32_t)ref, &q) == NTC_OK, "R19 window at %.2f C", t);
        double e = fabs(ntc_temp_mc(q) / 1000.0 - t);
        if (e > worst)
            worst = e;
        uint32_t q1;
        ntc_ratio_q16((int32_t)code + 1, (int32_t)ref, &q1);
        double res = fabs((double)(ntc_temp_mc(q) - ntc_temp_mc(q1))) / 1000.0;
        if (res > worst_res)
            worst_res = res;
    }
    /* software share of the R17 +-0.3 C: 0.05 C (partition.md P3; the ADC chain takes 0.067 C, conflicts.md C8) */
    CHECK(worst <= 0.05, "R17 software share: worst %.4f C <= 0.05 C", worst);
    CHECK(worst_res <= 0.1, "R17 resolution %.4f C <= 0.1 C", worst_res);
    printf("  R17 table + quantization error %.4f C (share 0.05), resolution %.4f C (limit 0.1)\n", worst, worst_res);
}

/* R9, rules 3, 4, 6: cold boot re-asserts both relays, one pulse at a time, drive / brake / coast */
static void test_relay_pulses(void)
{
    const uint8_t stored[2] = {1, 0};
    boot(3000, stored);
    sim.temp_c[0] = 19.0;        /* K1 should stay ON, K2 OFF: the re-assert pulses only */
    sim.temp_c[1] = 20.0;
    wake(0);
    CHECK(sim.nseg == 4, "two pulses, each drive + brake: %d segments", sim.nseg);
    if (sim.nseg == 4) {
        CHECK(sim.seg[0].relay == 0 && sim.seg[0].mode == SEG_FWD, "K1 set first");
        CHECK(seg_us(0) == BOARD_RELAY_PULSE_US, "K1 drive %llu us", (unsigned long long)seg_us(0));
        CHECK(seg_us(0) >= BOARD_RELAY_PULSE_MIN_US && seg_us(0) <= BOARD_RELAY_PULSE_MAX_US, "R9 window");
        CHECK(sim.seg[1].mode == SEG_BRAKE && seg_us(1) == BOARD_RELAY_BRAKE_US, "brake 5 ms");
        CHECK(sim.seg[2].relay == 1 && sim.seg[2].mode == SEG_REV, "K2 reset second");
        CHECK(sim.seg[2].t0 >= sim.seg[1].t1, "rule 4: no overlap");
        for (int i = 0; i < 4; i++)
            CHECK(sim.seg[i].sens, "SENS_EN (nSLEEP) high during the pulse");
    }
    CHECK(sim.contact[0] == 1 && sim.contact[1] == 0, "contacts K1 ON, K2 OFF");
    CHECK(sim.wdt_feeds > 1, "watchdog fed during the pulses (F2)");
}

/* rule 4 / F7: no pulse while Wi-Fi may transmit */
static void test_tx_inhibit(void)
{
    relays_t r;
    sim_reset();
    relays_init(&r);
    relays_request(&r, 0, 1);
    sim.wifi_busy = 1;
    relays_poll(&r, 0);
    CHECK(sim.nseg == 0 && r.phase == RLY_IDLE, "no drive while TX busy");
    sim.wifi_busy = 0;
    hal_gpio_out(BOARD_GPIO_SENS_EN, 1);
    relays_poll(&r, 1000);
    CHECK(r.phase == RLY_DRIVE, "drive once TX is idle");
}

/* R23: two readings at or below 2.0 V reset both relays OFF, then nothing switches */
static void test_cutoff(void)
{
    const uint8_t on2[2] = {1, 1};
    boot(2050, on2);
    sim.temp_c[0] = sim.temp_c[1] = 15.0;   /* both rooms want heat */
    wake(0);
    CHECK(rep.bat == BAT_OK && rep.guard_on, "2.05 V: alkaline, guard ON, no cut-off (%d mV)", rep.vbat_mv);
    sim.vbat_raw_mv = 1990;
    wake(60);
    CHECK(rep.bat == BAT_OK && sim.nseg == 0, "first low reading: not yet");
    wake(120);
    CHECK(rep.bat == BAT_CUTOFF_NOW, "second low reading: cut-off");
    CHECK(sim.nseg == 4 && sim.seg[0].mode == SEG_REV && sim.seg[2].mode == SEG_REV, "both relays reset");
    CHECK(sim.contact[0] == 0 && sim.contact[1] == 0, "both OFF");
    wake(180);
    CHECK(rep.bat == BAT_CUTOFF && sim.nseg == 0, "no switching after the cut-off");
}

/* R35 / rule 8: lithium cells never get the guard (and M4) ON; alkaline do, for the recharge */
static void test_guard(void)
{
    boot(3000, OFF2);
    wake(0);
    CHECK(rep.guard_on && sim.io2_low_max_us >= BOARD_GUARD_RECHARGE_US, "alkaline 3.0 V: guard ON for the recharge");

    boot(3700, OFF2);            /* Energizer L92 at 0 C: VBAT_P(off) 3.19 V, 50 us read 3.26 V (notes/circuit.md) */
    sim.vf_mv = 510;
    sim.rise50_mv = 70;
    uint64_t worst = 0;
    for (int k = 0; k < 10; k++) {
        wake(60u * k);
        if (sim.io2_low_max_us > worst)
            worst = sim.io2_low_max_us;
    }
    CHECK(worst < 1000, "lithium: IO2 low %llu us < 1 ms M4 turn-on (no +3V3 lift)", (unsigned long long)worst);

    /* P6: rule 8a as written stores the 50 us read (3.26 V < 3.35 V) and turns the guard ON on the next wake */
    bat_state_t b;
    bat_cold_boot(&b);
    int g1 = bat_guard_rule8a(&b, 3190);
    bat_store_reading(&b, g1, 3260);
    int g2 = bat_guard_rule8a(&b, 3190);
    CHECK(g1 == 0 && g2 == 1, "P6 reproduced: rule 8a as written alternates OFF / ON on lithium");
    printf("  P6: rule 8a as written -> wake 1 guard %s, wake 2 guard %s (lithium, 0 C)\n", g1 ? "ON" : "OFF",
           g2 ? "ON" : "OFF");
}

/* R4 / rule 8b: reversed cells at the start of a wake */
static void test_reverse(void)
{
    boot(3000, OFF2);
    sim.reversed = 1;
    wake(0);
    CHECK(rep.fault_reverse, "reverse detected");
    CHECK(sim.io2_low_max_us <= BOARD_VBAT_READ_WINDOW_US, "IO2 low only inside the 50 us window");
    CHECK(sim.nseg == 0 && !sim.is_out[BOARD_GPIO_SENS_EN], "no sensors, no relays");
}

/* R19 / F6: open and shorted probes */
static void test_probe_faults(void)
{
    const uint8_t on2[2] = {1, 1};
    boot(3000, on2);
    wake(0);
    sim.ntc_mode[0] = 1;
    sim.ntc_mode[1] = 2;
    wake(60);
    CHECK(rep.st[0] == NTC_OPEN && rep.st[1] == NTC_SHORT, "open / short flagged");
    CHECK(sim.contact[0] == 0 && sim.contact[1] == 0, "faulted rooms OFF at once");
    CHECK(sim.i2c_unpowered == 0 && sim.adc_bad_channel == 0, "no I2C to an unpowered U3, ADC channel from board.h");
}

/* control: hysteresis and the minimum dwell from the pulse budget */
static void test_control(void)
{
    room_t r = {.setpoint_mc = 20000};
    CHECK(control_decide(&r, 19700, NTC_OK, 0, 600) == 1, "below the band: ON");
    CHECK(control_decide(&r, 20300, NTC_OK, 60, 600) == 1, "above the band inside the dwell: stays ON");
    CHECK(control_decide(&r, 20300, NTC_OK, 600, 600) == 0, "after the dwell: OFF");
    CHECK(control_decide(&r, 20100, NTC_OK, 1300, 600) == 0, "inside the band: no change");
    printf("  pulse budget %d per day -> minimum dwell %d s (%.1f h) per relay\n", BOARD_RELAY_MAX_PULSES_PER_DAY,
           BOARD_RELAY_MIN_DWELL_S, BOARD_RELAY_MIN_DWELL_S / 3600.0);
}

/* P4: sensor-on time per control wake against the 10 ms the R6 sleep_floor testbench models */
static void test_wake_energy(void)
{
    boot(3000, OFF2);
    wake(0);
    wake(60);                     /* a wake with no relay change */
    double expect_us = 3.0 * 1e6 / BOARD_ADS_SPS;
    CHECK(rep.sens_on_us >= expect_us && rep.sens_on_us < expect_us + 5000, "3 conversions at %d SPS", BOARD_ADS_SPS);
    /* SENS_EN load 0.51 mA (U3 0.15, NTC dividers 2 x 0.165, R8 0.033) + light sleep 0.13 mA, battery at 2.6 V, 88 % */
    double extra_ua = 0.643 * (rep.sens_on_us - 10000.0) / 60e6 * 1e3 * 3.3 / (2.6 * 0.88);
    printf("  KNOWN P4: SENS_EN on %.1f ms per wake (sleep_floor models 10 ms): +%.1f uA at the battery, "
           "R6 headroom 0.47 uA\n", rep.sens_on_us / 1000.0, extra_ua);
}

int main(void)
{
    printf("fw host tests, board %s rev %s, contract %s\n", BOARD_PROJECT, BOARD_REVISION, BOARD_CONTRACT_ID);
    test_board_constants();
    test_ntc_accuracy();
    test_relay_pulses();
    test_tx_inhibit();
    test_cutoff();
    test_guard();
    test_reverse();
    test_probe_faults();
    test_control();
    test_wake_energy();
    printf("%d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
