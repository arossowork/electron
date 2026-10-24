# Mechanical envelope and interface review

2026-10-24. The agreed outline remains **140 × 80 mm**, four copper layers and nominal 1.6 mm thickness. The provisional enclosure allowance remains **160 × 100 × 40 mm**. Neither enclosure fit nor a particular purchased enclosure is approved by this note.

## USB receiver measured from manufacturer CAD

Source: [Amanero Combo768 USB-C STEP archive](https://www.amanero.com/mechanics/combo768usbc_3d.zip). Archive SHA256: `8d8db2c20a5f0ab21ed5ed678a84d1f6050b416a7269ea373b722cd382ec91c3`. Extracted `Combo768USBC.step` SHA256: `8f1cb148c49507d779ae2ecd1ffee367d49ce6c03539759d765206b9df199c6a`.

The STEP was read with OpenCascade/OCP, transferred to a single assembly shape and measured with `BRepBndLib.Add_s` and `Bnd_Box.CornerMin/CornerMax`. Assembly bounds are x = 0…32 mm, y = −0.758…68.5 mm, z = −3.400…8.455 mm: **32 × 69.258 × 11.855 mm overall**. The PCB solid measures **32 × 68.5 × 0.936 mm**. These are measurements of the supplied CAD, whose metadata dates to 2024, not confirmation of a delivered module revision. Temporary measurement inputs and JSON were retained at `/tmp/dac-mechanics/` during this run.

CAD mounting-hole axes are (3.5, 5.04), (28.5, 5.04), (3.5, 66), and (28.5, 66) mm, with 1.5 mm radius. The output header holes are on 2.54 mm pitch; the CAD does not establish electrical pin 1. The current board reserves the module vertically on the left, with a 15 mm height allowance including provisional support space. Module holes are not yet added to the motherboard: the support method and enclosure need to be chosen together.

## Header numbering and provisional harnesses

The [Amanero connector drawing](https://www.amanero.com/drivers/Combo768-D.pdf), page 3, numbers one row 1–10 and the opposite row 11–20. The stock KiCad odd/even footprint is electrically different. The project footprint `dac_audio:Combo768_Header_2x10_P2.54mm` preserves the stock socket pad/drill/courtyard geometry and remaps the opposing pairs to 1/11 through 10/20. Independent review confirmed this numbering against the drawing. Actual mating orientation, gender, cable construction and USB-C enclosure opening remain unverified. J100 represents a short harness interface; the drawing does not claim direct stacking compatibility.

J200 and J201 likewise represent panel-volume and headphone-jack harnesses. The actual stereo potentiometer, 6.35 mm jack, knob and panel cutouts remain to be selected. J300 uses the PJ-102AH footprint and a rear-facing provisional anchor. A floorplan fit cannot settle cable bend radius, connector access or panel tolerances.

## Internal reservations

The left module envelope occupies x = 2…36, y = 0…71 mm. The power region is x = 38…137, y = 2…35; DAC region x = 38…76, y = 43…78; analog region x = 80…137, y = 43…78. These replace the original approximate block locations while preserving the agreed outline. The power-to-DAC/analog region gap is 8 mm, above the declared 5 mm placement rule; it is not proof of noise isolation. Ground remains common.

The tallest selected motherboard part is the 4700 µF reservoir capacitor with a 22 mm maximum seated height. With 1.6 mm PCB, 4 mm lower standoffs and 2 mm top clearance the preliminary stack is 29.6 mm, within the 32 mm internal allowance. This calculation still requires real enclosure, harness and assembly checks.
