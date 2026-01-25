"""
data_sources.py
================

Every way statement/yield data gets INTO the database. Grouped in one file
because they answer the same question -- "where does the data come from" --
just with three different answers: a bundled real sample (offline), live
scraping (online), and a labelled synthetic dataset (fake, for the method-check).

PART 1 -- OFFLINE / ONLINE REAL DATA
  load_sample(conn)   reads the bundled sample_statements.json (real FOMC text
                      I saved) into the database. Always works, no network.
  fetch_real(conn)    the production path, run on my own machine: scrapes the
                      full set of FOMC statements from federalreserve.gov and
                      pulls 2y/10y yields from FRED. Needs open internet, which
                      the build sandbox does not have, so it's opt-in
                      (`--online`).

  Statement URL pattern (verified):
      https://www.federalreserve.gov/newsevents/pressreleases/monetary{YYYYMMDD}a.htm
  FRED CSV pattern:
      https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2   (and DGS10)

PART 2 -- SYNTHETIC DATA (the method-check)
  generate(conn)      builds a clearly-labelled fake dataset: statements that
                      drift slowly like a real policy cycle, and a yield series
                      where each statement day's move genuinely depends on that
                      statement's hawkish/dovish SHIFT, plus noise. If
                      analyze.py can recover THIS planted relationship, that's
                      evidence the analysis code works, before trusting it on
                      real, messier data. Everything here is tagged
                      source='SYNTHETIC' / is_synthetic=1 so it can never be
                      confused with the real FOMC statements.
"""

import json
import os
import re
import datetime
import random

from scoring import SCORED_FIELDS, ORDINALS
from scoring import index_of, delta_index  # noqa: F401 (index_of used by callers)
import db

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_statements.json")

# FOMC meeting dates I want for the full study. The bundled sample covers a
# spread of these; the rest are fetched online. Extend this list to widen the
# study (the eight-per-year schedule is published on the Fed's website).
MEETING_DATES = [
    "2015-01-28", "2015-12-16",
    "2020-03-15",
    "2022-03-16", "2022-06-15",
    "2024-09-18",
    # ... add the remaining 2015-2025 meeting dates here for the full run ...
]


# =============================================================================
# PART 1: REAL DATA (offline sample + online scrape)
# =============================================================================

def load_sample(conn):
    """Load the bundled real FOMC statements into the DB. Offline, always works."""
    with open(SAMPLE_PATH) as f:
        sample = json.load(f)
    n = 0
    for s in sample:
        db.upsert_statement(
            conn, source="FOMC", date=s["date"], text=s["text"],
            title=s.get("title"), url=s.get("url"), is_synthetic=False,
        )
        n += 1
    return n


def _strip_html(html):
    """Very small HTML -> text cleaner. Good enough for these statement pages;
    if the Fed restyles the page you may need to adjust the slice below."""
    html = re.sub(r"(?is)<script.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?</style>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)        # drop tags
    text = re.sub(r"&#x27;|&rsquo;", "'", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_fomc_statement(date_iso):
    """Download and clean one FOMC statement. Online; needs `requests`."""
    import requests  # imported here so the offline path has no dependency
    ymd = date_iso.replace("-", "")
    url = f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{ymd}a.htm"
    resp = requests.get(url, timeout=30, headers={"User-Agent": "research/edu"})
    resp.raise_for_status()
    text = _strip_html(resp.text)
    # keep the substantive body: from the first "Information"/"Recent"/"The"
    # paragraph through the voting block (so dissents survive).
    m = re.search(r"(Information received|Recent indicators|The coronavirus|"
                  r"Overall economic|Indicators of).*?(Last Update|For media)",
                  text)
    return (m.group(0) if m else text), url


def fetch_fred_series(series_id):
    """Download a FRED daily series as a list of (iso_date, value). Online."""
    import requests
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    resp = requests.get(url, timeout=60, headers={"User-Agent": "research/edu"})
    resp.raise_for_status()
    out = []
    for line in resp.text.splitlines()[1:]:  # skip header row
        parts = line.split(",")
        if len(parts) != 2:
            continue
        d, v = parts[0].strip(), parts[1].strip()
        if v in ("", "."):  # FRED uses '.' for missing
            continue
        try:
            out.append((d, float(v)))
        except ValueError:
            continue
    return out


def fetch_real(conn, dates=None):
    """Populate the DB from live sources. Run locally with internet."""
    dates = dates or MEETING_DATES
    n_stmt = 0
    for d in dates:
        text, url = fetch_fomc_statement(d)
        db.upsert_statement(conn, source="FOMC", date=d, text=text,
                            title="FOMC statement", url=url, is_synthetic=False)
        n_stmt += 1

    dgs2 = dict(fetch_fred_series("DGS2"))
    dgs10 = dict(fetch_fred_series("DGS10"))
    all_dates = sorted(set(dgs2) | set(dgs10))
    for d in all_dates:
        db.upsert_yield(conn, d, dgs2.get(d), dgs10.get(d), is_synthetic=False)
    return n_stmt, len(all_dates)


# =============================================================================
# PART 2: SYNTHETIC DATA (the method-check)
# =============================================================================

# How strongly the planted signal shows up, and how much noise drowns it.
# BETA small relative to NOISE keeps it realistic: the relationship is real but
# far from clean, like the real world.
BETA = 0.18      # yield move (in %) per unit of delta_index
NOISE_SD = 0.06  # daily noise std-dev on statement days


def _random_walk_records(n, seed):
    """Make n statement records that drift slowly, like a real policy cycle
    rather than independent coin flips."""
    rng = random.Random(seed)
    state = {f: 0 for f in SCORED_FIELDS}  # start everything neutral
    records = []
    for _ in range(n):
        for f in SCORED_FIELDS:
            vals = sorted(ORDINALS[f].values())
            if rng.random() < 0.45:  # 45% chance to nudge this field
                step = rng.choice([-1, 1])
                idx = vals.index(state[f])
                idx = max(0, min(len(vals) - 1, idx + step))
                state[f] = vals[idx]
        # convert ordinal ints back to category names for this record
        rec = {}
        for f in SCORED_FIELDS:
            inv = {v: k for k, v in ORDINALS[f].items()}
            rec[f] = inv[state[f]]
        rec["dissent_count"] = 0
        rec["quotes"] = {f: "[synthetic]" for f in SCORED_FIELDS}
        records.append(rec)
    return records


def _statement_dates(n, start, spacing_days):
    """n dates spaced ~6 weeks apart (FOMC-like cadence), nudged to weekdays."""
    dates = []
    d = start
    for _ in range(n):
        while d.weekday() >= 5:  # 5,6 = Sat,Sun
            d += datetime.timedelta(days=1)
        dates.append(d)
        d += datetime.timedelta(days=spacing_days)
    return dates


def generate(conn, n=60, seed=7):
    """Build n synthetic statements + a daily yield series with a planted signal.
    Returns the number of statements created."""
    rng = random.Random(seed * 31 + 1)
    records = _random_walk_records(n, seed)
    dates = _statement_dates(n, datetime.date(2001, 1, 3), 42)

    # --- write statements + their (no-LLM) extraction records ---
    stmt_ids = []
    for rec, dt in zip(records, dates):
        # a short readable blurb so the text column isn't empty; clearly fake
        blurb = "[SYNTHETIC] " + "; ".join(f"{f}={rec[f]}" for f in SCORED_FIELDS)
        sid = db.upsert_statement(
            conn, source="SYNTHETIC", date=dt.isoformat(),
            text=blurb, title="synthetic statement", url=None, is_synthetic=True,
        )
        stmt_ids.append(sid)
        # store the record straight into the extractions table (model=SYNTHETIC),
        # so the scoring step reads everything the same way regardless of origin
        db.save_extraction(
            conn, statement_id=sid, cache_key="synthetic",
            model="SYNTHETIC", schema_version="synthetic",
            raw_response=None, record=rec,
            created_at=datetime.datetime.utcnow().isoformat(),
        )

    # --- build a daily yield series with the signal injected on statement days ---
    # map statement date -> delta_index (None for the very first statement)
    deltas = {}
    for i in range(1, len(records)):
        deltas[dates[i]] = delta_index(records[i - 1], records[i])

    level2 = 2.0  # arbitrary starting 2y level, in percent
    span_start = dates[0] - datetime.timedelta(days=5)
    span_end = dates[-1] + datetime.timedelta(days=5)
    d = span_start
    while d <= span_end:
        if d.weekday() < 5:  # weekdays only, like real trading days
            if d in deltas:
                # planted relationship: hawkish shift -> yields up, plus noise
                move = BETA * deltas[d] + rng.gauss(0, NOISE_SD)
            else:
                move = rng.gauss(0, 0.02)  # quiet drift on ordinary days
            level2 += move
            dgs2 = round(level2, 2)
            dgs10 = round(level2 + 0.6 + rng.gauss(0, 0.03), 2)  # rough long end
            db.upsert_yield(conn, d.isoformat(), dgs2, dgs10, is_synthetic=True)
        d += datetime.timedelta(days=1)

    return n
