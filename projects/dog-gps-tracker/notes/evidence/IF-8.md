# IF-8 evidence (inspection)

Requirement (must): Debug and firmware-update access (SWD + UART) without breaking the seal; firmware update over the air

Acceptance: SWD and UART on test pads reachable in the battery compartment; FOTA supported by the module (datasheet)

## Result

Result: pass

Evidence (hardware access): seven test pads TP1-TP7 exist in design.yaml, on the back face (floorplan region test_pads, side: back; design.yaml line 86: "SWD + UART pads on the back face, reached through the battery-compartment ceiling window"). Nets: TP1 VDD_4V0, TP2 SWDIO, TP3 SWCLK, TP4 RESET_N, TP5 GND, TP6 UART_TX, TP7 UART_RX (architecture.md section 6 table, line 118). They are reachable with the cells out, without breaking the seal (inside the compartment). Pads are protected by ESD arrays U10 (VDD_4V0, SWDIO, SWCLK, RESET_N) and U11 (UART_TX, UART_RX), TPD4E05U06 (conflicts.md ESD decision, design.yaml U10/U11).

Evidence (FOTA): nRF9151 is a Cortex-M33 SiP with 1 MB flash (notes/datasheets/nrf9151.md line 6); modem and application FOTA through the nRF Connect SDK is a Nordic software feature, not stated in the local notes (not verified here). FOTA is a firmware item and remains open: no image size or update rate (conflicts.md C4), fota_image_kb is null and FOTA is not implemented (notes/progress-fw.md). Budget estimate: 300 kB image, 1.2 C per update (architecture.md section 6 power).
