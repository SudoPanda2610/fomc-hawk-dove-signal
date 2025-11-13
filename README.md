![FOMC Hawk Dove Signal banner](assets/banner.svg)

# FOMC Hawk Dove Signal

A transparent pipeline for extracting monetary-policy language from FOMC statements and comparing changes in stance with two-year Treasury yield moves.

## Research hypothesis

Asset prices react to policy surprises rather than the absolute level of policy language. The primary signal is therefore the change in the hawkishness index relative to the previous statement. The level remains a secondary descriptive measure.

## Design

The LLM is limited to structured extraction. It maps statement language into a fixed schema and returns supporting excerpts. Deterministic code then validates fields, applies ordinal mappings and weights, calculates the index, and runs the market-alignment analysis.

This separation keeps numerical logic inspectable and allows extractions to be cached and reviewed.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py --mode synthetic
python -m pytest test_properties.py -q
```

The bundled statement sample can be loaded offline:

```bash
python main.py --mode fetch
```

Live extraction requires an Anthropic API key. Online statement and yield collection is optional:

```bash
python main.py --mode fetch --online
python main.py --mode extract
python main.py --mode score --source FOMC
python main.py --mode analyze --source FOMC
```

## Repository layout

```text
scoring.py              Schema, ordinal maps, weights, and index logic
data_sources.py         Offline sample, online sources, and synthetic generator
extract.py              Validated language extraction
db.py                   SQLite cache and result storage
analyze.py              Signal and yield-move analysis
main.py                 Command-line pipeline
test_properties.py      Property tests for deterministic logic
```

## Validation and limitations

The synthetic experiment checks whether the analysis recovers a planted change signal before it is applied to real statements. Real meeting samples remain small. Statements are only one part of FOMC communication, and the change index is an imperfect proxy for market expectations. Results are descriptive rather than a trading claim.
