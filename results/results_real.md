# Same-day statement/yield association (real)

Source: `FOMC`; model: `session-default-model (id not exposed by completion helper)`; schema: `v1`.
Coverage: 2021-01-27 through 2025-12-10; 40/40 statements scored; 40 matched; 0 excluded.

## Descriptive metrics

- LEVEL vs same-day DGS2 move: Spearman rho=-0.273, p=0.089; n=40.
- CHANGE vs same-day DGS2 move: Spearman rho=0.256, p=0.115; n=39.
- Nonzero sign agreement: 0.583; 14/24 agree.

## Window and interpretation

- Daily DGS2 percent yields; movement is 100 × (same-day minus latest prior observation), in basis points.
- Only weekday statements with same-day DGS2 and a prior observation within seven calendar days are included. No next-day substitution.
- Level uses every matched event; change excludes the first statement; sign agreement additionally excludes zero changes or zero moves.
- These are descriptive contemporaneous associations, not event surprises, forecasts, causal estimates, or intraday reactions.
- The index includes the realized rate action, which confounds any language-only interpretation. Changes between statements are not measured market surprises.
- Daily yields also reflect other news, press conferences and projections. Small-sample p-values are exploratory, not validation claims.
- Spearman metrics are undefined for fewer than three pairs or constant inputs; JSON represents undefined metrics as null.

## Exclusions

- None.

## Per-event audit

Full records, extraction provenance and yield observations: `results_real.json`.

| Date | Index | Change | Prior yield date | Prior DGS2 | Same DGS2 | Move (bp) | Status |
|---|---:|---:|---|---:|---:|---:|---|
| 2021-01-27 | -0.600 | n/a | 2021-01-26 | 0.110 | 0.120 | 1.000 | included |
| 2021-03-17 | -0.500 | 0.100 | 2021-03-16 | 0.150 | 0.130 | -2.000 | included |
| 2021-04-28 | 0.000 | 0.500 | 2021-04-27 | 0.170 | 0.170 | 0.000 | included |
| 2021-06-16 | 0.000 | 0.000 | 2021-06-15 | 0.160 | 0.210 | 5.000 | included |
| 2021-07-28 | -0.200 | -0.200 | 2021-07-27 | 0.200 | 0.200 | 0.000 | included |
| 2021-09-22 | 0.000 | 0.200 | 2021-09-21 | 0.220 | 0.250 | 3.000 | included |
| 2021-11-03 | 0.100 | 0.100 | 2021-11-02 | 0.460 | 0.470 | 1.000 | included |
| 2021-12-15 | 0.200 | 0.100 | 2021-12-14 | 0.670 | 0.690 | 2.000 | included |
| 2022-01-26 | 0.400 | 0.200 | 2022-01-25 | 1.020 | 1.130 | 11.000 | included |
| 2022-03-16 | 0.800 | 0.400 | 2022-03-15 | 1.850 | 1.950 | 10.000 | included |
| 2022-05-04 | 0.900 | 0.100 | 2022-05-03 | 2.780 | 2.660 | -12.000 | included |
| 2022-06-15 | 1.000 | 0.100 | 2022-06-14 | 3.450 | 3.200 | -25.000 | included |
| 2022-07-27 | 0.600 | -0.400 | 2022-07-26 | 3.020 | 2.960 | -6.000 | included |
| 2022-09-21 | 0.700 | 0.100 | 2022-09-20 | 3.960 | 4.020 | 6.000 | included |
| 2022-11-02 | 0.700 | 0.000 | 2022-11-01 | 4.540 | 4.610 | 7.000 | included |
| 2022-12-14 | 0.700 | 0.000 | 2022-12-13 | 4.220 | 4.230 | 1.000 | included |
| 2023-02-01 | 0.600 | -0.100 | 2023-01-31 | 4.210 | 4.090 | -12.000 | included |
| 2023-03-22 | 0.500 | -0.100 | 2023-03-21 | 4.170 | 3.960 | -21.000 | included |
| 2023-05-03 | 0.500 | 0.000 | 2023-05-02 | 3.970 | 3.890 | -8.000 | included |
| 2023-06-14 | 0.400 | -0.100 | 2023-06-13 | 4.670 | 4.740 | 7.000 | included |
| 2023-07-26 | 0.500 | 0.100 | 2023-07-25 | 4.850 | 4.820 | -3.000 | included |
| 2023-09-20 | 0.500 | 0.000 | 2023-09-19 | 5.080 | 5.120 | 4.000 | included |
| 2023-11-01 | 0.600 | 0.100 | 2023-10-31 | 5.070 | 4.950 | -12.000 | included |
| 2023-12-13 | 0.500 | -0.100 | 2023-12-12 | 4.730 | 4.460 | -27.000 | included |
| 2024-01-31 | 0.400 | -0.100 | 2024-01-30 | 4.360 | 4.270 | -9.000 | included |
| 2024-03-20 | 0.400 | 0.000 | 2024-03-19 | 4.680 | 4.590 | -9.000 | included |
| 2024-05-01 | 0.400 | 0.000 | 2024-04-30 | 5.040 | 4.960 | -8.000 | included |
| 2024-06-12 | 0.400 | 0.000 | 2024-06-11 | 4.810 | 4.750 | -6.000 | included |
| 2024-07-31 | 0.400 | 0.000 | 2024-07-30 | 4.350 | 4.290 | -6.000 | included |
| 2024-09-18 | 0.300 | -0.100 | 2024-09-17 | 3.590 | 3.610 | 2.000 | included |
| 2024-11-07 | 0.300 | 0.000 | 2024-11-06 | 4.270 | 4.210 | -6.000 | included |
| 2024-12-18 | 0.400 | 0.100 | 2024-12-17 | 4.250 | 4.350 | 10.000 | included |
| 2025-01-29 | 0.400 | 0.000 | 2025-01-28 | 4.190 | 4.210 | 2.000 | included |
| 2025-03-19 | 0.500 | 0.100 | 2025-03-18 | 4.040 | 3.990 | -5.000 | included |
| 2025-05-07 | 0.400 | -0.100 | 2025-05-06 | 3.780 | 3.780 | 0.000 | included |
| 2025-06-18 | 0.400 | 0.000 | 2025-06-17 | 3.940 | 3.940 | 0.000 | included |
| 2025-07-30 | 0.200 | -0.200 | 2025-07-29 | 3.860 | 3.940 | 8.000 | included |
| 2025-09-17 | 0.100 | -0.100 | 2025-09-16 | 3.510 | 3.520 | 1.000 | included |
| 2025-10-29 | 0.200 | 0.100 | 2025-10-28 | 3.470 | 3.590 | 12.000 | included |
| 2025-12-10 | 0.100 | -0.100 | 2025-12-09 | 3.610 | 3.540 | -7.000 | included |
