# IF-4 evidence (datasheet)

Requirement (should): Position accuracy for finding the dog

Acceptance: horizontal CEP50 <= 5 m open sky (receiver datasheet)

## Result

Result: pass

Evidence: Quectel LC76G Series GNSS Specification V1.1 (https://quectel.com/content/uploads/2024/03/Quectel_LC76G_Series_GNSS_Specification_V1.1-1-1.pdf), performance table: horizontal position accuracy, autonomous, 1.5 m CEP50 (24 h static, -130 dBm, more than 6 SVs); the same value for the AB and PA variants (the project uses LC76G-PA, U8). 1.5 m is at most 5 m. The older V1.0 spec lists 2.0 m, still within the limit. The value was read from search results of the Quectel and mirror copies of the spec, not from the PDF text; confirm the page number in the first article. Caveat: the 12 x 12 patch costs about 1 dB gain (architecture.md section 16), and an in-use CEP depends on antenna and ground plane, which this datasheet figure does not include.
