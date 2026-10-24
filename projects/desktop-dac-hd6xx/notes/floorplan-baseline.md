# First floorplan baseline, 2026-10-24

This is an architecture feasibility check with eight estimated blocks and zero real components. It is not a PCB routing benchmark or an electrical verification result.

| Check | Observed result |
| --- | --- |
| Original container setup | FAILED: fetched latest router 0.23.7 against pinned 0.23.1 source, then attempted a source build without Cargo. |
| Container setup with matching release pin | PASS: image build exited 0; router imported as 0.23.1. The engine source is unchanged. |
| Placeholder floorplan | PASS in 4.9 s reported engine time; 0 placement violations, 51% front-face area fill, 23.6 mm stack against a provisional 32 mm limit. |
| Power/analog region geometry | PASS: 8.49 mm between declared regions against the provisional 5 mm spacing. This is not a copper/noise check. |
| Impossible DAC-region fixture | Expected FAIL in 5.2 s: `dac_stage (24.0 x 22.0 mm) does not fit in region dac (10.0 x 10.0 mm)`. The negative fixture was in a temporary container directory; the source design was not changed. |
| Requirements trace | Expected UNVERIFIED: 10 total, 10 unverified, 0 covered. No electrical/product requirement was declared complete. |
| Circuit / SPICE / routing / DRC / physical bench | NOT CHECKED. |

The reported seconds are the engine's elapsed floorplan time from one run each, excluding toolchain installation and container startup. They do not establish solver performance or repeatability. Both used the default 20,000 moves and two seeds. `build/FLOORPLAN.md`, `build/floorplan.svg` and `build/status.json` are regenerated outputs, not hand-edited source.

## Reproduction metadata

- Engine: `ca10fa8e0b9d67745787734b979e784f4422a3ec` with unchanged `src/hwflow`.
- Environment-only correction: pin KRT binary release `v0.23.1` alongside source `78e3c35330fd38206b7d4773f315ce2bbeea2718` and check the crate version. All 15 Rust source files were compared with that release and matched.
- Image: `hwflow-audio:ca10fa8e`, ID `cbbf1f8964d7f2193794f5300bd515fd10fabcc2d1beacc053c49550a94ddd3e`.
- KiCad 10.0.7, ngspice 42, Python 3.11.15, uv 0.11.33, KRT Rust extension 0.23.1.
- Initial rootless build network could not resolve Ubuntu package hosts. Building with `--network=host` resolved that setup issue without changing host network settings. Validation containers had networking disabled.
- Source `design.yaml` SHA-256: `fe610ee3d496e00b2a831669e543949a1109c12c549312fd35240004739e8229`.

From this checkout, with the image above built, reproduce the positive check (Podman-compatible Docker CLI; `--userns=keep-id` preserves the host file owner):

```sh
docker run --rm --network=none --userns=keep-id \
  --mount type=bind,src="$(pwd)/projects/desktop-dac-hd6xx",dst=/app/projects/desktop-dac-hd6xx \
  hwflow-audio:ca10fa8e hwflow floorplan projects/desktop-dac-hd6xx
```

For the negative check, copy only `design.yaml` to a temporary project and change the `dac` region rectangle from `[80, 4, 108, 35]` to `[80, 4, 90, 14]`; run the same floorplan command on that copy. Require a nonzero exit and the named fit failure, not merely any error. Run `hwflow trace` on the original project and require zero covered requirements at this stage.
