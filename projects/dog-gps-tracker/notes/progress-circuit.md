# progress: stage 4 circuit (2026-11-01)
done: full capture (power, modem, LTE match, eSIM, GNSS, UI, sensor, debug; 83 parts); models/dog_power.lib, models/dog_fets.lib; lib symbols TPS612994_YBH, TPS3838K33_DBV; back footprints; interface.yaml (mcu U1, BUZ_P/N, GNSS back-power rule, divider refs); budgets; notes/circuit.md.
builds: 6 x --until sim; last: check 0 errors, ERC 0 errors, sim 10/10 pass. floorplan PASS with risks.
open: hwflow fw 1 error (no nRF91 family table in src/hwflow/fw: tool gap); TPL5010 R7 value; nRF9151 SAADC accuracy; TPS7A02 DBV re-read.
half-edited: none (design.yaml consistent).
next: stage-5 review batch applied; floorplan FAIL on copper_len budgets (quick-placer, see circuit.md); lead decides fixed U1/U8 positions or RF blocks.
