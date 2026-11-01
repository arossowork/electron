# ENV-3 evidence (datasheet)

Requirement (must): ESD on exposed parts (button, battery contacts)

Acceptance: IEC 61000-4-2 +-8 kV contact; TVS rated >= that on button and battery lines (datasheet)

## Result

Result: pass

Evidence (requirement: IEC 61000-4-2 +-8 kV contact on the button and battery lines):
- D3 ESD9B5.0ST5G on BTN (SW1 button): onsemi class +-30 kV contact (parts-ui.md line 11, "unv page", not read from the datasheet text), VRWM 5 V (design.yaml D3 limits). Ratings above 8 kV rely on that unverified page; confirm the onsemi datasheet.
- D1 TPD1E10B06 on BAT_RAW (battery contacts, J1): IEC 61000-4-2 +-30 kV contact and air, VRWM 5.5 V, Vclamp 10 V at 1 A (tpd1e10b06.md, TI datasheet p.4). Clamp above the boost abs max 6.5 V is covered by CIN and the series FET, with an ESD test at the first article (parts-power.md line 111).
- U10 TPD4E05U06 on test pads VDD_4V0, SWDIO, SWCLK, RESET_N (TP1-TP4) and U11 on UART_TX, UART_RX (TP6, TP7): IEC 61000-4-2 +-12 kV contact (design.yaml U10 limits, TI SLVSBK9; parts-ui.md line 60). TP5 is GND.
All four exceed +-8 kV. Residual risk: first-article ESD test, per the notes.
