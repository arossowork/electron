# home_thermostat: build report
Updated 2026-10-20 00:50:27. Source: design.yaml.
Verdict: PASS


- check: OK (0 errors, 0 warnings)
- ERC: OK (0 errors, 2 warnings)
  - warning isolated_pin_label: Label connected to only one pin ["Global Label 'AC_NC1'"]
  - warning isolated_pin_label: Label connected to only one pin ["Global Label 'AC_NC2'"]
- SPICE: 163/163 measures pass
  - approximate models (built-in, datasheet fit, behavioral or no `spice.source`): D1, K1, K2, M1, M2, M3, M4, U2, U4, U5, U6; justify each in notes/models.md or use a vendor model
- derating: OK; top dissipation U2 0.0 mW, R20 0.0 mW, U6 0.0 mW
- Monte Carlo sleep_dc: yield 100% over 100 runs (v3v3_sleep_2v2 3.399..3.399; v3v3_sleep_3v6 3.399..3.399; r2_pack_sleep_uw 30.98..30.99; r2_rail_sleep_ua 6.705..6.705; vbat_div_leak_ua 1.432e-05..1.45e-05; r17_tap_minus_vdd_sleep -3.404..-3.404)
- Monte Carlo sleep_wc: yield 100% over 100 runs (r2_pack_sleep_uw_wc 72.57..72.61; r2_rail_sleep_ua_wc 13.71..13.71; r2_rail_v_wc 3.471..3.471; s2_rail_max_li_wc 3.471..3.471)
- Monte Carlo reverse_tran: yield 99% over 100 runs (s1_vbat_at_rev 2.659..2.743; s1_gate_at_rev 2.737..2.811; s1_vin_min 0.2694..0.2959; s1_sw_min 0.2694..0.2959; s1_irev_peak_a 4.592..4.918; s1_irev_settled_ua 0.8144..0.8274; s1_gate_settled 0.5053..0.5463; s1_vin_min_vthmin 0.2525..2.198; s1_sw_min_vthmin 0.2525..2.198; s1_irev_peak_a_vthmin 2.198..4.925; s1_irev_settled_ua_vthmin 0.8144..2.198e+06; s1_t_hi_us_vthmin 9.7..2.198e+06; s1_q_uc_vthmin 21.87..2.198e+06; s1_vin_min_vthtyp 0.2575..2.198)
- Monte Carlo power_tran: yield 100% over 100 runs (r3_vmin_cutoff 3.215..3.231; r3_ipack_avg_cutoff 0.279..0.2791; r3_vin_tx_cutoff 1.633..1.634; r3_vmin_lithium 3.283..3.283; r3_vmin_alkaline 3.283..3.283; r3_vmax 3.399..3.399; r3_vin_min 1.633..1.634; r16_vcoil1_min 3.072..3.072; r16_vcoil2_min 3.072..3.072; r16_pulse1_ms 41..41; r16_pulse2_ms 41..41; r16_vrail_min 3.273..3.295; r9_droop_reset_mv 7.427..28.86; r7_vcoil1_min 3.072..3.072; r7_vcoil2_min 3.072..3.072; r7_pulse1_ms 41..41; r7_pulse2_ms 41..41; r7_vrail_min 3.273..3.295; r9_droop_set_mv 7.427..28.86; r7_hold_ua -0.007632..5.487e-07; r17_vbat_err_mv 16.99..20.6; r17_tap_minus_vdd -2.572..-2.57; r9_avdd_on_droop_mv 26.71..30.64)
- Monte Carlo tx_ilim: yield 100% over 100 runs (r3_vin_tx_065 1.633..1.634; r3_vrail_065 3.215..3.231; r3_vin_tx_085 1.459..1.459; r3_vrail_085 3.269..3.273; r3_vin_tx_110 1.349..1.35; r3_vrail_110 3.283..3.283)
- Monte Carlo powerup: yield 100% over 100 runs (pu_v3v3_at_en_typ 3.292..3.368; pu_t_en_typ_ms 287.8..3292; pu_v3v3_at_en_min 3.292..3.341; pu_t_en_min_ms 204..3292; pu_v3v3_at_en_max 3.292..3.381; pu_t_en_max_ms 406.1..3292; pu_vmax_lithium 3.399..3.399)
- Monte Carlo reverse_ins: yield 100% over 100 runs (rev_i_pack_ua 0.5174..0.5256; rev_vin_min -0.02481..-0.01974; rev_rail_max -2.749e-07..-2.749e-07; rev_i_pack_ua_li 0.9336..0.9485; rev_vin_min_li -0.0404..-0.0321)
- Monte Carlo ntc_tran: yield 100% over 100 runs (r4_ch1_5c -0.4069..-0.3917; r4_ch1_20c -0.2882..-0.2855; r4_ch1_35c -0.1663..-0.1624; r4_ch2_5c -0.03991..0.0453; r4_ch2_20c -0.1911..-0.1869; r4_ch2_35c -0.3731..-0.3672; r5_ch1_35c_cable -0.1678..-0.1639; r5_cable_err_c 0.001467..0.001468; r4u_ch1_5c -0.5624..-0.5094; r4u_ch1_20c -0.4396..-0.3808; r4u_ch1_35c -0.3192..-0.2542; r4u_ch2_5c -0.1973..0.1884; r4u_ch2_20c -0.3504..-0.2596; r4u_ch2_35c -0.5352..-0.4351; r4_cal_hold_a 0.1754..0.1759; r4_cal_hold_b 0.1752..0.1755)
- Monte Carlo hum_47hz: yield 100% over 100 runs (r5_hum_atten_47_db 25.61..25.62; r5_hum_atten_47_db_w198 24.03..24.03; r5_hum_atten_47_db_w202 27.52..27.52)
- Monte Carlo hum_53hz: yield 100% over 100 runs (r5_hum_atten_53_db 26.48..26.49; r5_hum_atten_53_db_w198 28.43..28.43; r5_hum_atten_53_db_w202 24.93..24.93)
- layout: OK; board [130, 40] mm; router 0 unrouted, krt 4 pass(es); 529 segments, 94 vias; DRC 0 errors / 77 warnings; unrouted 0
- post-layout: OK; board 5200 mm2, 4 layers, 1443.2 mm copper, 94 vias
  - warn decap-distance: C8 is 4.3 mm of copper from U1 on +3V3 (limit 4.0 mm)
  - IR drop +3V3: 0.00 mV (worst at U2.B2)
  - IR drop AVDD_SW: 0.00 mV (worst at M2.3)
  - IR drop BAT_N: 0.00 mV (worst at BT1.2)
  - IR drop VBAT: 0.00 mV (worst at BT1.1)
  - post-layout SPICE: 163/163 pass; 29 measures moved more than 1%
    - vbat_div_leak_ua: 1.44e-05 -> 6.461e-05 uA
    - vbat_div_leak_ua: 1.494e-05 -> 6.515e-05 uA
    - s1_vin_min: 0.2413 -> 0.2617 V
    - s1_sw_min: 0.2413 -> 0.3012 V
    - s1_irev_peak_a: 2.939 -> 2.869 A
    - s1_gate_settled: 0.4896 -> 0.5441 V
    - s1_vin_min_vthmin: 0.2285 -> 0.2479 V
    - s1_sw_min_vthmin: 0.2285 -> 0.2902 V
- fab outputs: OK (build/fab)
- requirements: 21/21 verified, 0 failing, 0 unverified
- BOM cost: $27.94 per board
