![FOMC Hawk Dove Signal banner](assets/banner.svg)

# FOMC Hawk Dove Signal

A pipeline that classifies the language of official FOMC statements into a fixed schema, scores each statement on a hawkish–dovish index, and measures how changes in that index line up with same-day moves in the 2-year Treasury yield.

It runs on **real data**: 40 official Federal Reserve statements (every scheduled meeting from 2021 through 2025) and daily FRED `DGS2` yields.

## Result

| Measure | Value | n |
|---|---|---|
| Index **level** vs same-day 2y yield change (Spearman) | ρ = −0.273 (p = 0.089) | 40 |
| Index **change** vs same-day 2y yield change (Spearman) | ρ = 0.256 (p = 0.115) | 39 |
| Direction agreement, change vs move | 14 of 24 (58.3%; 50% = coin flip) | 24 |

**This is a null result.** Neither association is significant at the 5% level, and with 39 events the study could only detect a large effect. 12 of the 39 changes were exactly zero, and 4 yield moves were zero, which reduces the information in the direction test. The sample is exploratory; the p-values are not corrected for the several measures reported.

Full per-event table and provenance: [`results/results_real.md`](results/results_real.md) and [`results/results_real.json`](results/results_real.json).

## How labels were produced, and their limits

- The LLM sees **only the statement text**, never yields or outcomes, and fills a six-field schema with a verbatim supporting quote per field. Every quote is checked to be an exact excerpt of the statement; off-menu values or fabricated quotes fail the import.
- No API key was configured for the recorded run, so the classification was done by the model behind an agent session. **Its exact identifier is not exposed and is recorded as such.** The annotations are committed in [`results/annotations.json`](results/annotations.json), so the whole study can be re-run without any key. The Anthropic path in `extract.py` remains available for independent reclassification.
- **Labelling is noisy.** Two complete runs agreed on the index for only **31 of 40** statements (mean absolute difference 0.04, worst case 0.40). Single-run label noise biases correlations toward zero, so these estimates are probably conservative, and they are not exact.
- **Validity handling.** The quote cap was raised from 40 to 60 words after one two-dissenter statement could not be quoted in fewer; this was decided before any yield data was examined, and all 40 statements were then re-classified under the single final prompt. One statement needed a second attempt after an off-menu answer. Retries were triggered only by invalid output, never by a label.
- **`rate_action` is derived, not guessed.** The size of the rate decision is arithmetic over each statement's stated target range, so it is computed in code (`extract.derive_rate_actions`) and used to cross-check the model. The model disagreed once: for 2022-09-21, a 75 bp hike, it answered `hike_25`; the derived value `hike_50plus` replaced it, and the override is stored in the audit record.

## What this does and does not show

- The yield move is the **daily** close-to-close change in DGS2, not an intraday reaction. It also reflects other news, the press conference and projections.
- The index includes the realized rate decision, which confounds any language-only reading.
- A change in index is a rough proxy for surprise; it is not a measured market surprise, and nothing here is a forecast or a trading result.

## Reproduce

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python main.py --mode fetch  --db output/fomc_real.db          # official statements + FRED yields
python main.py --mode import --db output/fomc_real.db --annotations results/annotations.json
python main.py --mode score   --db output/fomc_real.db
python main.py --mode analyze --db output/fomc_real.db --label real
python -m pytest -q
```

`fetch` pulls the statement list from the Federal Reserve's meeting calendar and verifies each URL against the listed meeting date. Raw responses are cached with SHA-256 hashes under `output/source_cache`. `import` refuses any annotation whose statement text no longer matches its recorded hash.

To reclassify yourself instead of importing the recorded run: `export ANTHROPIC_API_KEY=...` and use `--mode extract`.

## Modes

| Mode | Purpose |
|---|---|
| `fetch` | Download statements (`--start-year`, `--end-year`) and daily yields |
| `export-requests` | Write each statement and its prompt for an external LLM run |
| `import` | Validate and store a recorded annotation file |
| `extract` | Classify through the Anthropic API |
| `score` | Compute the index and its change; refuses to bridge a missing extraction |
| `analyze` | Same-day yield association, with a per-event audit |
| `synthetic` | Unit test of the code path only (see below) |

## Synthetic check

`--mode synthetic` fabricates 60 statements and yields with a **planted** relationship and confirms the code recovers it. It validates the plumbing and says nothing about markets; never cite its correlation as a finding. Real and synthetic observations are stored separately and cannot overwrite each other.

## Layout

```text
scoring.py        Schema, ordinal maps, weights, index and change
data_sources.py   Calendar and statement parsing, FRED yields, synthetic generator
extract.py        Prompt, strict validation, import/export, rate-action derivation
db.py             SQLite storage with real/synthetic isolation
analyze.py        Scoring driver and same-day alignment with per-event audit
main.py           Command-line pipeline
test_*.py         Property tests for scoring; regression tests for the real-data path
results/          Recorded annotations and the real-data report
```
