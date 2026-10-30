"""Complete-source scoring and descriptive same-day daily-yield associations.

Every statement must have a text-bound extraction from one model/schema before
scoring. Real and synthetic yields are never mixed. A usable yield window is a
weekday statement date with an observed same-day DGS2 and the latest preceding
DGS2 observation no more than seven calendar days earlier. No next-day fallback.
"""

import datetime
import json
import math
from bisect import bisect_left
from collections import Counter
from pathlib import Path

from scipy.stats import spearmanr

import db
from scoring import SCHEMA_VERSION, change_vector, delta_index, index_of

MAX_PRIOR_GAP_DAYS = 7


def _extractions(conn, source):
    statements = db.get_statements(conn, source)
    if not statements:
        raise ValueError(f"No statements for {source}; fetch or generate them first")
    selected = []
    for statement in statements:
        if statement["is_synthetic"] != db.source_flag(source):
            raise ValueError("statement source/is_synthetic mismatch")
        row = conn.execute("SELECT * FROM extractions WHERE statement_id=? "
                           "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                           (statement["id"],)).fetchone()
        if row is None:
            raise ValueError(f"Missing extraction for {source} {statement['date']}; complete all statements before scoring")
        digest = db.text_digest(statement["text"])
        if row["text_sha256"] != digest:
            raise ValueError(f"Extraction text hash missing or stale for {statement['date']}; re-extract or import")
        if source == "FOMC":
            if not row["model"].strip() or row["model"].upper() == "SYNTHETIC":
                raise ValueError("Real scoring requires a real extraction model, not SYNTHETIC")
            if row["schema_version"] != SCHEMA_VERSION:
                raise ValueError(f"Real scoring requires schema {SCHEMA_VERSION}")
        elif row["model"] != "SYNTHETIC" or row["schema_version"] != "synthetic":
            raise ValueError("Synthetic scoring requires SYNTHETIC model/synthetic schema")
        record = json.loads(row["record_json"])
        # index_of validates all six categorical scoring fields before any writes.
        index = index_of(record)
        selected.append((statement, row, record, index))
    versions = {(row["model"], row["schema_version"]) for _, row, _, _ in selected}
    if len(versions) != 1:
        raise ValueError("Mixed extraction model/schema; re-extract the complete source consistently")
    return selected


def score_all(conn, source):
    """Atomically replace a source's scores, never bridging an extraction gap."""
    db.source_flag(source)
    conn.execute("SAVEPOINT score_source")
    try:
        selected = _extractions(conn, source)
        db._invalidate_scores(conn, source)
        previous_id = previous_record = None
        for statement, extraction, record, index in selected:
            delta = delta_index(previous_record, record) if previous_record is not None else None
            changes = change_vector(previous_record, record) if previous_record is not None else None
            db.save_score(conn, statement["id"], index, previous_id, delta, changes,
                          extraction_cache_key=extraction["cache_key"], model=extraction["model"],
                          schema_version=extraction["schema_version"], text_sha256=extraction["text_sha256"],
                          commit=False)
            previous_id, previous_record = statement["id"], record
    except Exception:
        conn.execute("ROLLBACK TO score_source")
        conn.execute("RELEASE score_source")
        raise
    conn.execute("RELEASE score_source")
    return len(selected)


def _load_yields(conn, source):
    rows = conn.execute("SELECT date,dgs2,dgs10 FROM yields WHERE is_synthetic=? ORDER BY date",
                        (db.source_flag(source),)).fetchall()
    for row in rows:
        datetime.date.fromisoformat(row["date"])
        for key in ("dgs2", "dgs10"):
            if row[key] is not None and not math.isfinite(row[key]):
                raise ValueError(f"Nonfinite {key} for {row['date']}")
    return rows


def _spearman(xs, ys):
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return None, None
    rho, p = spearmanr(xs, ys)
    return (float(rho) if math.isfinite(rho) else None,
            float(p) if math.isfinite(p) else None)


def run_alignment(conn, source, label, out_path):
    """Write Markdown plus strict JSON containing every included/excluded event."""
    flag = db.source_flag(source)
    rows = conn.execute("""SELECT s.*, sc.index_value, sc.delta_index,
        sc.prior_statement_id, sc.change_vector_json, sc.extraction_cache_key,
        sc.model AS score_model, sc.schema_version AS score_schema,
        sc.text_sha256 AS score_text_sha256,
        p.date AS prior_statement_date
        FROM statements s JOIN scores sc ON sc.statement_id=s.id
        LEFT JOIN statements p ON p.id=sc.prior_statement_id
        WHERE s.source=? ORDER BY s.date""", (source,)).fetchall()
    if not rows:
        raise ValueError(f"No scores for {source}; complete extraction and scoring first")
    selected = _extractions(conn, source)
    if len(rows) != len(selected):
        raise ValueError("Incomplete source scores; rerun score_all")
    extraction_by_id = {s["id"]: (e, record) for s, e, record, _ in selected}
    previous_id = None
    for row in rows:
        extraction, _ = extraction_by_id[row["id"]]
        if (row["is_synthetic"] != flag or row["prior_statement_id"] != previous_id
                or row["score_text_sha256"] != db.text_digest(row["text"])
                or row["extraction_cache_key"] != extraction["cache_key"]
                or row["score_model"] != extraction["model"]
                or row["score_schema"] != extraction["schema_version"]):
            raise ValueError("Stale or inconsistent score provenance; rerun score_all")
        if not math.isfinite(row["index_value"]) or (row["delta_index"] is not None and not math.isfinite(row["delta_index"])):
            raise ValueError("Scores must be finite")
        previous_id = row["id"]

    yields = _load_yields(conn, source)
    by_date = {row["date"]: row for row in yields}
    observed = [row for row in yields if row["dgs2"] is not None]
    observed_dates = [row["date"] for row in observed]
    events = []
    exclusions = Counter()
    for row in rows:
        date = datetime.date.fromisoformat(row["date"])
        same = by_date.get(row["date"])
        position = bisect_left(observed_dates, row["date"])
        prior = observed[position - 1] if position else None
        gap = (date - datetime.date.fromisoformat(prior["date"])).days if prior else None
        reason = None
        if date.weekday() >= 5:
            reason = "weekend_statement"
        elif same is None or same["dgs2"] is None:
            reason = "missing_same_day_dgs2"
        elif prior is None:
            reason = "missing_prior_dgs2"
        elif gap > MAX_PRIOR_GAP_DAYS:
            reason = "prior_gap_exceeds_7_days"
        extraction, record = extraction_by_id[row["id"]]
        event = {
            "statement_id": row["id"], "date": row["date"], "source": source,
            "is_synthetic": flag, "url": row["url"], "text_sha256": row["score_text_sha256"],
            "model": extraction["model"], "schema_version": extraction["schema_version"],
            "cache_key": extraction["cache_key"], "prompt_version": extraction["prompt_version"],
            "extraction_created_at": extraction["created_at"],
            "extraction_provenance": json.loads(extraction["provenance_json"]) if extraction["provenance_json"] else None,
            "record": record, "index_value": row["index_value"], "delta_index": row["delta_index"],
            "change_vector": json.loads(row["change_vector_json"]) if row["change_vector_json"] else None,
            "prior_statement_id": row["prior_statement_id"], "prior_statement_date": row["prior_statement_date"],
            "prior_yield_date": prior["date"] if prior else None,
            "same_yield_date": same["date"] if same else None, "prior_gap_days": gap,
            "prior_dgs2": prior["dgs2"] if prior else None,
            "same_dgs2": same["dgs2"] if same else None,
            "prior_dgs10": prior["dgs10"] if prior else None,
            "same_dgs10": same["dgs10"] if same else None,
            "delta_dgs2_bps": None, "delta_dgs10_bps": None,
            "included": reason is None, "exclusion_reason": reason,
        }
        if reason is None:
            event["delta_dgs2_bps"] = 100 * (same["dgs2"] - prior["dgs2"])
            if same["dgs10"] is not None and prior["dgs10"] is not None:
                event["delta_dgs10_bps"] = 100 * (same["dgs10"] - prior["dgs10"])
        else:
            exclusions[reason] += 1
        events.append(event)
    included = [event for event in events if event["included"]]
    if not included and source == "FOMC":
        raise ValueError("No matched real same-day yield windows; check actual DGS2 coverage")
    changes = [event for event in included if event["delta_index"] is not None]
    nonzero = [event for event in changes if event["delta_index"] != 0 and event["delta_dgs2_bps"] != 0]
    agree = sum((event["delta_index"] > 0) == (event["delta_dgs2_bps"] > 0) for event in nonzero)
    first_extraction = selected[0][1]
    provenance = {
        "source": source, "is_synthetic": flag, "model": first_extraction["model"],
        "schema_version": first_extraction["schema_version"],
        "prompt_versions": sorted({e["prompt_version"] for _, e, _, _ in selected if e["prompt_version"] is not None}),
        "yield_series": ["DGS2", "DGS10"], "yield_unit": "percent",
        "move_unit": "basis_points", "yield_is_synthetic": flag,
        "window": "same_day_minus_latest_prior_observation",
        "max_prior_gap_calendar_days": MAX_PRIOR_GAP_DAYS,
        "method_check_only": source == "SYNTHETIC",
    }
    out = {
        "label": label, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "n_statements": len(selected), "n_scored": len(rows), "n_matched": len(included),
        "n_excluded": len(events) - len(included), "exclusions": dict(sorted(exclusions.items())),
        "n_level": len(included), "n_delta": len(changes), "n_sign": len(nonzero), "n_agree": agree,
        "sign_agreement": agree / len(nonzero) if nonzero else None,
        "provenance": provenance,
        "coverage": {"first_statement_date": rows[0]["date"], "last_statement_date": rows[-1]["date"],
                     "statement_dates": [row["date"] for row in rows], "complete_source_scoring": True,
                     "n_real_statements": len(selected) if source == "FOMC" else 0,
                     "first_yield_date": yields[0]["date"] if yields else None,
                     "last_yield_date": yields[-1]["date"] if yields else None},
        "events": events,
    }
    out["level_rho"], out["level_p"] = _spearman(
        [event["index_value"] for event in included], [event["delta_dgs2_bps"] for event in included])
    out["delta_rho"], out["delta_p"] = _spearman(
        [event["delta_index"] for event in changes], [event["delta_dgs2_bps"] for event in changes])
    denominators = {"level_rho": out["n_level"], "level_p": out["n_level"],
                    "delta_rho": out["n_delta"], "delta_p": out["n_delta"],
                    "sign_agreement": out["n_sign"]}
    with conn:
        for metric, denominator in denominators.items():
            db.save_result(conn, label, metric, out[metric], denominator, out["created_at"],
                           provenance=provenance, commit=False)
        for metric in ("n_statements", "n_scored", "n_matched", "n_excluded", "n_level", "n_delta", "n_sign", "n_agree"):
            db.save_result(conn, label, metric, out[metric], None, out["created_at"],
                           provenance=provenance, commit=False)
    _write_report(out, out_path)
    return out


def _fmt(value):
    return "n/a" if value is None else f"{value:.3f}"


def _write_report(out, path):
    path = Path(path)
    json_path = path.with_suffix(".json")
    if path == json_path:
        raise ValueError("out_path must be a Markdown/report path, not .json")
    provenance = out["provenance"]
    lines = [
        f"# Same-day statement/yield association ({out['label']})", "",
        f"Source: `{provenance['source']}`; model: `{provenance['model']}`; schema: `{provenance['schema_version']}`.",
        f"Coverage: {out['coverage']['first_statement_date']} through {out['coverage']['last_statement_date']}; "
        f"{out['n_scored']}/{out['n_statements']} statements scored; {out['n_matched']} matched; {out['n_excluded']} excluded.",
        "", "## Descriptive metrics", "",
        f"- LEVEL vs same-day DGS2 move: Spearman rho={_fmt(out['level_rho'])}, p={_fmt(out['level_p'])}; n={out['n_level']}.",
        f"- CHANGE vs same-day DGS2 move: Spearman rho={_fmt(out['delta_rho'])}, p={_fmt(out['delta_p'])}; n={out['n_delta']}.",
        f"- Nonzero sign agreement: {_fmt(out['sign_agreement'])}; {out['n_agree']}/{out['n_sign']} agree.",
        "", "## Window and interpretation", "",
        "- Daily DGS2 percent yields; movement is 100 × (same-day minus latest prior observation), in basis points.",
        "- Only weekday statements with same-day DGS2 and a prior observation within seven calendar days are included. No next-day substitution.",
        "- Level uses every matched event; change excludes the first statement; sign agreement additionally excludes zero changes or zero moves.",
        "- These are descriptive contemporaneous associations, not event surprises, forecasts, causal estimates, or intraday reactions.",
        "- The index includes the realized rate action, which confounds any language-only interpretation. Changes between statements are not measured market surprises.",
        "- Daily yields also reflect other news, press conferences and projections. Small-sample p-values are exploratory, not validation claims.",
        "- Spearman metrics are undefined for fewer than three pairs or constant inputs; JSON represents undefined metrics as null.",
    ]
    if provenance["method_check_only"]:
        lines += ["- SYNTHETIC METHOD CHECK ONLY: generated associations are not evidence about actual FOMC statements or markets."]
    lines += ["", "## Exclusions", ""]
    lines += [f"- {reason}: {count}" for reason, count in out["exclusions"].items()] or ["- None."]
    lines += ["", "## Per-event audit", "", f"Full records, extraction provenance and yield observations: `{json_path.name}`.", "",
              "| Date | Index | Change | Prior yield date | Prior DGS2 | Same DGS2 | Move (bp) | Status |",
              "|---|---:|---:|---|---:|---:|---:|---|"]
    for event in out["events"]:
        lines.append(f"| {event['date']} | {_fmt(event['index_value'])} | {_fmt(event['delta_index'])} | "
                     f"{event['prior_yield_date'] or 'n/a'} | {_fmt(event['prior_dgs2'])} | {_fmt(event['same_dgs2'])} | "
                     f"{_fmt(event['delta_dgs2_bps'])} | {event['exclusion_reason'] or 'included'} |")
    payload = json.dumps(out, indent=2, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(payload, encoding="utf-8")
