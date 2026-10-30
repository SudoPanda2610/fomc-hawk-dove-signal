"""Behavioural tests for the real-data path.

Each test pins a failure that actually occurred or a guarantee that protects the
validity of the study: parsing real calendar markup, deriving the rate decision
from the documents, keeping real and synthetic observations apart, and refusing
to score incomplete or fabricated annotations. None of these tests call a network
or an LLM.
"""
import json
import math
import tempfile
import unittest
from pathlib import Path

import analyze
import data_sources
import db
import extract

BASE = "https://www.federalreserve.gov/newsevents/pressreleases/monetary"


def statement(day, tail):
    return {"date": day, "text": "The Committee decided to " + tail}


def record(inflation="balanced"):
    return {"inflation_stance": inflation, "growth_labor_stance": "moderate",
            "rate_action": "hold", "balance_sheet": "neutral",
            "forward_guidance": "neutral", "dissent_direction": "none",
            "dissent_count": 0,
            "quotes": {f: "x" for f in ("inflation_stance", "growth_labor_stance",
                                        "rate_action", "balance_sheet",
                                        "forward_guidance", "dissent_direction")}}


def add_extraction(conn, stmt_id, text, rec):
    db.save_extraction(conn, stmt_id, "k" + str(stmt_id), "test-model", "v1", "{}", rec,
                       "2026-01-01T00:00:00+00:00", text_sha256=db.text_digest(text),
                       prompt_version=extract.PROMPT_VERSION, provenance={"provider": "test"})


class RateActionDerivationTests(unittest.TestCase):
    def test_size_comes_from_consecutive_stated_ranges_including_75bp(self):
        # Wording and non-breaking hyphens copied from real 2022-2024 statements.
        rows = [
            statement("2022-06-15", "raise the target range for the federal funds rate to 1\u20111/2 to 1-3/4 percent and"),
            statement("2022-07-27", "raise the target range for the federal funds rate to 2-1/4 to 2-1/2 percent and"),
            statement("2022-09-21", "raise the target range for the federal funds rate to 3 to 3-1/4 percent and"),
            statement("2023-07-26", "maintain the target range for the federal funds rate at 5-1/4 to 5-1/2 percent."),
            statement("2024-11-07", "lower the target range for the federal funds rate by 1/4 percentage point to 4-1/2 to 4-3/4 percent."),
        ]
        actions = extract.derive_rate_actions(rows)
        self.assertIsNone(actions["2022-06-15"])          # no prior range, no stated size
        self.assertEqual(actions["2022-07-27"], "hike_50plus")   # +75bp
        self.assertEqual(actions["2022-09-21"], "hike_50plus")   # +75bp, the case the LLM mislabelled
        self.assertEqual(actions["2023-07-26"], "hold")
        self.assertEqual(actions["2024-11-07"], "cut_25")        # stated size, not "percentage" cut short

    def test_missing_decision_sentence_fails_loudly(self):
        with self.assertRaises(ValueError):
            extract.derive_rate_actions([{"date": "2022-01-01", "text": "No decision here."}])


class CalendarParsingTests(unittest.TestCase):
    @staticmethod
    def page(month, days, slug, extra=""):
        return f"""<div class="panel"><div class="panel-heading">2023 FOMC Meetings</div>
        <div class="fomc-meeting"><div class="fomc-meeting__month">{month}</div>
        <div class="fomc-meeting__date">{days}</div>
        <div><strong>Statement:</strong><a href="/newsevents/pressreleases/monetary{slug}a.htm">HTML</a></div>
        </div>{extra}</div>"""

    def test_two_month_meeting_uses_release_day_and_notation_votes_are_skipped(self):
        vote = ('<div class="fomc-meeting"><div class="fomc-meeting__month">August</div>'
                '<div class="fomc-meeting__date">22 (notation vote)</div>'
                '<a href="/newsevents/pressreleases/monetary20230822a.htm">Statement on Goals</a></div>')
        html = self.page("Jan/Feb", "31-1", "20230201", extra=vote)
        self.assertEqual(data_sources.parse_calendar(html, 2023, 2023),
                         [("2023-02-01", BASE + "20230201a.htm")])

    def test_url_that_disagrees_with_listed_meeting_date_is_rejected(self):
        with self.assertRaises(ValueError):
            data_sources.parse_calendar(self.page("Jan/Feb", "31-3", "20230201"), 2023, 2023)

    def test_regular_meeting_without_statement_is_an_error_not_a_smaller_sample(self):
        html = '<div class="panel"><div class="panel-heading">2023 FOMC Meetings</div>' \
               '<div class="fomc-meeting"><div class="fomc-meeting__month">March</div>' \
               '<div class="fomc-meeting__date">21-22</div></div></div>'
        with self.assertRaises(ValueError):
            data_sources.parse_calendar(html, 2023, 2023)


class ExtractionValidationTests(unittest.TestCase):
    TEXT = "Inflation remains elevated. The Committee decided to maintain the target range."

    def test_quotes_must_be_verbatim_excerpts(self):
        good = record("elevated")
        good["quotes"] = {f: "Inflation remains elevated." for f in good["quotes"]}
        good["quotes"]["balance_sheet"] = ""
        self.assertIs(extract.validate_extraction(good, self.TEXT), good)
        fabricated = json.loads(json.dumps(good))
        fabricated["quotes"]["inflation_stance"] = "Inflation is soaring."
        with self.assertRaises(ValueError):
            extract.validate_extraction(fabricated, self.TEXT)

    def test_empty_quote_is_only_allowed_when_absence_is_the_label(self):
        bad = record("elevated")
        bad["quotes"] = {f: "Inflation remains elevated." for f in bad["quotes"]}
        bad["quotes"]["inflation_stance"] = ""
        with self.assertRaises(ValueError):
            extract.validate_extraction(bad, self.TEXT)


class StorageAndAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init_db(self.conn)

    def test_real_and_synthetic_yields_on_the_same_date_do_not_overwrite(self):
        db.upsert_yield(self.conn, "2021-01-27", 0.12, 0.90, False)
        db.upsert_yield(self.conn, "2021-01-27", 5.00, 5.60, True)
        rows = {r["is_synthetic"]: r["dgs2"] for r in
                self.conn.execute("SELECT is_synthetic, dgs2 FROM yields")}
        self.assertEqual(rows, {0: 0.12, 1: 5.00})

    def test_scoring_refuses_to_bridge_a_missing_extraction(self):
        texts = ["first statement", "second statement"]
        ids = [db.upsert_statement(self.conn, "FOMC", d, t, is_synthetic=False)
               for d, t in zip(("2021-03-17", "2021-04-28"), texts)]
        add_extraction(self.conn, ids[0], texts[0], record())      # second one is missing
        with self.assertRaisesRegex(ValueError, "Missing extraction"):
            analyze.score_all(self.conn, "FOMC")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0], 0)

    def test_real_alignment_uses_only_real_same_day_yields(self):
        # inflation path -> index deltas +1, -2, +3, -2 (in ordinal steps of 0.1)
        path = ["balanced", "firming", "soft", "elevated", "balanced"]
        moves = [0.0, 0.01, -0.03, 0.05, -0.03]     # real same-day moves, percent, rank-aligned
        dates = ["2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22"]
        for k, (day, infl, move) in enumerate(zip(dates, path, moves)):
            text = f"statement {k}"
            add_extraction(self.conn, db.upsert_statement(self.conn, "FOMC", day, text,
                                                           is_synthetic=False), text, record(infl))
            prior = (analyze.datetime.date.fromisoformat(day) - analyze.datetime.timedelta(days=1)).isoformat()
            db.upsert_yield(self.conn, prior, 2.0, 3.0, False)
            db.upsert_yield(self.conn, day, 2.0 + move, 3.0, False)
            # Contaminating synthetic yields with the OPPOSITE ordering on the very same dates.
            db.upsert_yield(self.conn, prior, 2.0, 3.0, True)
            db.upsert_yield(self.conn, day, 2.0 - move, 3.0, True)
        self.assertEqual(analyze.score_all(self.conn, "FOMC"), 5)
        with tempfile.TemporaryDirectory() as tmp:
            out = analyze.run_alignment(self.conn, "FOMC", "real", str(Path(tmp) / "r.md"))
        self.assertEqual(out["n_matched"], 5)
        self.assertTrue(math.isclose(out["delta_rho"], 1.0), out["delta_rho"])


if __name__ == "__main__":
    unittest.main()
