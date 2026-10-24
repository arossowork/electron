# openEMS spike: optional field-solver stage

Status: prototype (`src/hwflow/verify/em.py`, `hwflow em <proj>`). Machine: macOS arm64, 10 cores, 24 GB.

## Installation (works, no sudo, about 10 minutes)

```
git clone --recursive https://github.com/thliebig/openEMS-Project.git   # v0.37.0, 974294b
brew install cmake boost hdf5 cgal vtk                                   # bottles, 7.5 min
uv venv -p 3.11 ~/opt/openEMS/venv
VIRTUAL_ENV=~/opt/openEMS/venv uv pip install pip numpy h5py matplotlib cython setuptools setuptools_scm wheel
# with VIRTUAL_ENV=~/opt/openEMS/venv and its bin/ first on PATH:
./update_openEMS.sh ~/opt/openEMS --disable-GUI --python --skip-dep-check   # 2 min 26 s, builds TinyXML itself
~/opt/openEMS/venv/bin/python -c "import CSXCAD, openEMS"                 # openEMS 0.37.0
```

Did not work: `brew search openems` / `csxcad` (no formula; the openEMS docs say the old formula is
broken and unmaintained); conda-forge `openems` / `csxcad` (not on anaconda.org); PyPI `openEMS` /
`CSXCAD` (not published). Install size 139 MB plus the Homebrew deps (VTK is the large one).

`hwflow em` re-executes itself under `~/opt/openEMS/venv/bin/python` (or `HWFLOW_OPENEMS_PYTHON`)
when the bindings are not importable from the project venv, and prints "skipped" when they are absent.

## Validation: straight line, 30 mm, w 2.9 mm, h 1.6 mm, er 4.3, 3 GHz (`em.msl_line`)

MSL ports with 20 mm feed sections, PML at the line ends, zero-thickness PEC strip.

| mesh (lambda/N, substrate cells) | cells | run | Z0 openEMS | eeff (S21 phase) | max S11 | S21 @ 3 GHz |
|---|---|---|---|---|---|---|
| /30, 4 | 64 k | 4.2 s, 87 MB | 52.9 ohm | 3.28 | -32 dB | -0.10 dB (tand 0.02) |
| /60, 8 | 396 k | 21 s, 127 MB | 53.4 ohm | 3.29 | -30 dB | +0.05 dB (lossless) |

Hammerstad-Jensen: 52.2 ohm (t=0), 51.7 ohm (t=35 um), eeff 3.25. Agreement 1.5-3 %.

Thin traces (w 0.25 mm, the board's default) need fine z meshing: Z0 = 119 / 130 / 137 ohm for
4 / 8 / 16 substrate cells against 139 ohm (t=0) and 133 ohm (t=35 um). Grounded CPW (same-layer
pour, 16 cells): gap 0.2 mm 86.3 ohm against 84.1 ohm conformal mapping (`em.cpwg_z0`).

## Real board: examples/sensor_preamp (19 x 15 mm, 2 layers, GND pour both sides)

`em.simulate_net`: net segments as polygons, vias, filled GND zones from the file, GND tracks and
pads, FR-4 (tand 0.02 as conductivity), 50 ohm lumped ports from F.Cu pad to a B.Cu patch, MUR box.

| net, ports | fmax, cell | cells | run, RSS | Z_TDR plateau | microstrip formula | CPWG formula |
|---|---|---|---|---|---|---|
| SENS_IN U2.3 -> J1.2, 12.1 mm | 6 GHz, 0.1 mm | 768 k | 124 s, 165 MB | 86.5 ohm | 133.4 ohm | 85.3 ohm |
| AMP_OUT J1.3 -> U2.1, 11.6 mm | 3 GHz, 0.2 mm | 216 k | 17 s, 108 MB | 55 ohm (stubs, coarse) | 133.4 | 90.3 |
| AMP_OUT | 20 GHz, 0.1 mm | 824 k | 75 s, 176 MB | 67.5 ohm | 133.4 | 90.3 |
| AMP_OUT | 20 GHz, 0.05 mm | 3.07 M | 360 s, 434 MB | 69.3 ohm | 133.4 | 90.3 |

The main finding: the closed-form segment Z0 in `postlayout.py` (133 ohm) is 50 % high on this board,
because the trace runs 0.2-0.57 mm from a same-layer GND pour (a grounded CPW, not a microstrip).
A conformal-mapping CPWG formula with the measured pour gap gets within 2 % on SENS_IN (a simple
two-pad net). AMP_OUT has two extra stubs (R1, C4 pads) that pull the TDR plateau down further.

Pitfalls met (all handled in em.py): default NrTS is 1e9 (runs never end without a cap); MSL ports
inside a PML give nonsense; PML on all six sides with a conductive substrate went unstable; the
staircased pour touches 0.2 mm-clearance traces at 0.1-0.2 mm cells and shorts the net (fix: a
dielectric keepout of at least 1.5 cells around the net); KiCad 10 writes `(net "GND")` in zones.

## Cost and scaling

FDTD work = cells x steps. Cells scale with board area / cell^2 x z-lines; steps scale with
1/min-cell (Courant) times the ring-down time. In practice: halving the cell gives about 5x run time
(75 s -> 360 s); fmax has little direct effect at a fixed cell (it sets the cell needed). A whole
50 x 50 mm 2-layer board at 0.1 mm is about 5 M cells: 8-15 min and about 0.6 GB per net, single
process. Memory is small; time is the limit. Per-net cropping (bounding box of the net plus a margin)
keeps it to 1-3 min per net on boards of this size.

## Which analyses are worth adding

| analysis | verdict for an autonomous loop |
|---|---|
| Z0 / TDR of a critical net | Realistic (1-3 min per net). Use it to calibrate the closed form, not as the main check. |
| Crosstalk between two nets | Realistic for one pair (4-port, same mesh, about 2x the run). Gate on the existing heuristic. |
| Ground cut / return path | Realistic: compare S21 and TDR with and without the B.Cu cut; slots are geometry already in the file. |
| Buck hot loop, near field / radiated | Too fragile: needs switch models, a time-domain source, nf2ff and a far box; results not robust to setup. Keep the loop-area heuristic. |
| Antennas | Feasible but not autonomous: needs a matching network, a large air box, nf2ff; hours of tuning per design. Out of scope. |

## Proposed integration

- design.yaml: `nets.<NET>.em: true` or `em: {fmax_ghz: 6, ports: [J1.3, U2.1], cell_mm: 0.1,
  z_target: 50, z_tol: 0.1, max_steps: 60000}`; optional `board.em: {enabled: false, budget_s: 600}`.
- Command: `hwflow em <proj> [--net X] [--fmax-ghz F] [--cell-mm C]`, never part of default `build`
  (or `build --em`), writes `build/em/<net>.json` and `status.json["em"]`.
- Report line: `- em: SENS_IN Z_tdr 86 ohm (closed form 85, target 50 FAIL), S21 -0.96 dB @ 6 GHz; 124 s`.
- Scorecard: `em_nets`, `em_pass_ratio`, `em_runtime_s`, `em_max_z_err` (not in the eval total until stable).
- Gate: only nets with `em:` set, or `impedance_ohm` / class `high_speed` / `clock` with an edge rate
  whose knee frequency puts the net length above lambda/10; skip when openEMS is absent.
- Cheaper first step: add the CPWG formula (pour gap from the zone fill) to `postlayout.py` now.
