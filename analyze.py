"""
analyze.py
==========

Two jobs:

  score_all()      walk the statements in date order, compute each one's index
                   and its change vs the prior statement, and store both.

  run_alignment()  the actual question -- does the hawkish/dovish signal line up
                   with the move in the 2-year Treasury yield around the meeting?

IMPORTANT FRAMING (this is the honest part, and the part an interviewer will push
on, so I write it into the output itself):

  * The next-day yield move reflects the SURPRISE relative to what markets already
    expected, not the absolute stance. So my LEVEL index should relate only weakly
    to the move. My CHANGE signal (delta_index) is a rough surprise proxy and
    should relate more.
  * N is tiny (~8 meetings/year). These are DESCRIPTIVE associations, not a
    validated predictor. I report sign-agreement and rank correlation, with
    p-values shown but not leaned on.
  * The statement is only part of the information set -- the press conference and
    projections move rates too. I score only the statement, on purpose.

The yield-move definitions:
  prior  = last available yield strictly BEFORE the statement date
  same   = yield ON the statement date (FOMC releases at 2pm, so the close already
           contains most of the reaction)
  next   = first available yield AFTER the statement date
  dy_same = same - prior      dy_next = next - prior
"""

import datetime

from scipy.stats import spearmanr

import db
from scoring import SCORED_FIELDS, index_of, delta_index, change_vector


# ---- scoring driver --------------------------------------------------------

def _record_for(conn, statement_id):
    """Fetch the stored record for a statement (real LLM cache OR synthetic)."""
    row = conn.execute(
        "SELECT record_json FROM extractions WHERE statement_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (statement_id,),
    ).fetchone()
    if row is None:
        return None
    import json
    return json.loads(row["record_json"])


def score_all(conn, source):
    """Compute and store index + delta + change vector for each statement that
    has an extraction. Returns how many were scored."""
    statements = db.get_statements(conn, source=source)
    prev_id, prev_rec = None, None
    scored = 0
    for s in statements:
        rec = _record_for(conn, s["id"])
        if rec is None:
            # no extraction yet (e.g. real text but no API key) -- skip quietly
            continue
        idx = index_of(rec)
        if prev_rec is None:
            db.save_score(conn, s["id"], idx, None, None, None)
        else:
            d = delta_index(prev_rec, rec)
            cv = change_vector(prev_rec, rec)
            db.save_score(conn, s["id"], idx, prev_id, d, cv)
        prev_id, prev_rec = s["id"], rec
        scored += 1
    return scored


# ---- yield lookup helpers --------------------------------------------------

def _load_yields(conn):
    rows = conn.execute(
        "SELECT date, dgs2 FROM yields WHERE dgs2 IS NOT NULL ORDER BY date"
    ).fetchall()
    return [(r["date"], r["dgs2"]) for r in rows]


def _yield_window(yields, statement_date):
    """Return (prior, same, next) 2y yields around a statement date, or Nones
    where unavailable. `yields` is a sorted list of (iso_date, value)."""
    prior = same = nxt = None
    for d, v in yields:
        if d < statement_date:
            prior = v            # keep advancing -> ends on last before date
        elif d == statement_date:
            same = v
        elif d > statement_date:
            nxt = v              # first one after
            break
    return prior, same, nxt


# ---- the alignment analysis ------------------------------------------------

def run_alignment(conn, source, label, out_path):
    """Compute level/change vs yield-move associations and write a short report.
    Returns a dict of the headline numbers (also useful for the tests/demo)."""
    yields = _load_yields(conn)

    # pull each scored statement with its date
    rows = conn.execute(
        "SELECT s.date AS date, sc.index_value AS idx, sc.delta_index AS delta "
        "FROM scores sc JOIN statements s ON s.id = sc.statement_id "
        "WHERE s.source=? ORDER BY s.date",
        (source,),
    ).fetchall()

    levels, deltas, dy_same_l, dy_next_l = [], [], [], []
    matched = 0
    for r in rows:
        prior, same, nxt = _yield_window(yields, r["date"])
        if prior is None or (same is None and nxt is None):
            continue  # can't form a move for this date
        ref_same = same if same is not None else nxt
        dy_same = ref_same - prior
        dy_next = (nxt - prior) if nxt is not None else dy_same
        matched += 1
        levels.append(r["idx"])
        dy_same_l.append(dy_same)
        dy_next_l.append(dy_next)
        if r["delta"] is not None:
            deltas.append((r["delta"], dy_same))

    out = {"n_matched": matched}

    def _spearman(xs, ys):
        if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
            return (float("nan"), float("nan"))
        rho, p = spearmanr(xs, ys)
        return (float(rho), float(p))

    # LEVEL vs same-day move (expected weak)
    out["level_rho"], out["level_p"] = _spearman(levels, dy_same_l)

    # CHANGE vs same-day move (expected stronger) + sign agreement
    if deltas:
        dvals = [d for d, _ in deltas]
        moves = [m for _, m in deltas]
        out["delta_rho"], out["delta_p"] = _spearman(dvals, moves)
        nonzero = [(d, m) for d, m in deltas if d != 0 and m != 0]
        if nonzero:
            agree = sum(1 for d, m in nonzero if (d > 0) == (m > 0))
            out["sign_agreement"] = agree / len(nonzero)
            out["n_sign"] = len(nonzero)
        else:
            out["sign_agreement"], out["n_sign"] = float("nan"), 0
    else:
        out["delta_rho"] = out["delta_p"] = float("nan")
        out["sign_agreement"], out["n_sign"] = float("nan"), 0

    # save headline numbers to the results table (SQL history)
    now = datetime.datetime.utcnow().isoformat()
    for metric in ["level_rho", "delta_rho", "sign_agreement"]:
        db.save_result(conn, label, metric, out.get(metric), matched, now)

    _write_report(out, label, source, out_path)
    return out


def _fmt(x):
    return "n/a" if x != x else f"{x:.3f}"  # x!=x is True only for NaN


def _write_report(out, label, source, path):
    lines = [
        f"# Statement -> rate-move alignment ({label})",
        "",
        f"Source: `{source}`  |  statements with a usable yield window: "
        f"{out['n_matched']}",
        "",
        "## Headline numbers",
        "",
        f"- LEVEL index vs same-day 2y move (Spearman rho): "
        f"{_fmt(out['level_rho'])}  (p={_fmt(out['level_p'])})",
        f"- CHANGE (delta_index) vs same-day 2y move (Spearman rho): "
        f"{_fmt(out['delta_rho'])}  (p={_fmt(out['delta_p'])})",
        f"- Sign agreement, change vs move: {_fmt(out.get('sign_agreement'))} "
        f"(n={out.get('n_sign', 0)}; 0.50 = coin flip)",
        "",
        "## How to read this (and what NOT to claim)",
        "",
        "- The yield move is driven by the **surprise vs expectations**, not the",
        "  absolute stance. So a weak LEVEL correlation is expected, not a failure.",
        "  The CHANGE signal is a rough surprise proxy and should do better.",
        "- N is tiny. Treat these as **descriptive** associations. The p-values are",
        "  shown for honesty, not to claim significance.",
        "- The statement is only part of the news on a meeting day (press conference,",
        "  projections). I scored the statement only, by design.",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
