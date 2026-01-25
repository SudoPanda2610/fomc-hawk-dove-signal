# FOMC hawkish/dovish signal

Read a central-bank policy statement with an LLM, extract a **structured** read of
its language into a fixed schema, turn that into a transparent hawkish/dovish
**index**, and check (descriptively) how the *shift* in that index lines up with the
move in the 2-year Treasury yield around the meeting.

The LLM does **extraction only** — the part it is reliable at and that I can
validate. Everything that turns text into a number, and number into an analysis,
is plain deterministic code I can defend line by line. This is **not** "an LLM
predicts the market."

## The one idea worth remembering

The next-day yield move reflects the **surprise vs what markets expected**, not the
absolute stance. So:

- the **level** of hawkishness should relate only *weakly* to the yield move;
- the **change vs the prior statement** (a rough surprise proxy) should relate *more*.

The synthetic method-check below shows exactly this pattern, and `analyze.py`
writes these caveats into its own output so I never over-claim.

## How to run

```bash
pip install -r requirements.txt

# 1) Method-check on labelled synthetic data (no API key, no network):
python main.py --mode synthetic

# 2) Real statements (bundled sample) -> structured extraction -> score -> analyze:
export ANTHROPIC_API_KEY=sk-...        # extraction needs this
python main.py --mode all

# Stages can also be run one at a time:
python main.py --mode fetch            # load the bundled real sample (offline)
python main.py --mode fetch --online   # scrape the full set from the web (local only)
python main.py --mode extract          # LLM extraction, cached to SQLite
python main.py --mode score  --source FOMC
python main.py --mode analyze --source FOMC

# Tests:
python -m pytest test_properties.py -q
```

## What the synthetic run shows

I generate fake statements that drift like a real policy cycle, and a yield series
where each meeting-day move genuinely depends on that statement's hawkish/dovish
*shift*, plus noise. The analysis recovers the planted signal:

| signal                         | Spearman rho vs same-day 2y move |
|--------------------------------|----------------------------------|
| LEVEL (absolute hawkishness)   | ~0.22                            |
| CHANGE (delta vs prior)        | ~0.50  (sign agreement ~0.76)    |

The change beats the level — the relationship I built in is the one the code finds.
If it couldn't even find a signal I planted, I wouldn't trust it on real data.

## Files

| file | role |
|------|------|
| `scoring.py` | fixed enums + ordinal maps + weights (schema), plus index / diff logic — pure, deterministic, no I/O |
| `data_sources.py` | offline loader for the bundled real sample, online FOMC/FRED fetchers, and the labelled synthetic generator (planted-signal method-check) |
| `db.py` | SQLite storage (statements, cached extractions, scores, yields, results) |
| `extract.py` | the only LLM use: read a statement -> validated record, cached in SQLite |
| `analyze.py` | scoring driver + the level/change-vs-yield alignment, with caveats |
| `main.py` | one flag-driven entry point (`--mode`) |
| `test_properties.py` | property-based tests on the deterministic layer (`scoring.py`) |
| `sample_statements.json` | 5 real FOMC statements spanning regimes (dovish hold → emergency cut → first hike → peak-hawkish → dovish pivot, incl. a real dissent) |
| `concept_note.md` | background + interview questions (kept separate from the build) |

## Honest limitations (state these before anyone asks)

- **Surprise, not level.** See above. The level index is not expected to predict
  the move; only the change is a (rough) surprise proxy. True surprise would need
  pre-meeting market expectations (e.g. fed-funds futures), which I did not include.
- **Tiny N.** ~8 meetings/year. Everything here is descriptive. P-values are shown
  for honesty, not significance.
- **Statement only.** The press conference and the dot plot also move rates on a
  meeting day. I scored the statement on purpose; it explains only part of the news.
- **The LLM step is the soft spot.** It is validated by hand-labelling a handful of
  statements and comparing (see `concept_note.md`), and every field comes with a
  supporting quote so I can spot-check. It is cached so results are reproducible.

## What ran where

Built and verified in a sandbox with **no internet to the Fed/FRED and no API key**,
so: the synthetic pipeline and all tests ran fully; the real-data extraction +
real-yield join are designed to run on my own machine (`--mode all` with a key, and
`--mode fetch --online` for the full statement set and live yields). The bundled
sample is real statement text so the real path has genuine data to start from.
