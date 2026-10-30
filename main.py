"""FOMC statement-language pipeline.

Real-data stages (official Federal Reserve statements, FRED DGS2/DGS10):

  fetch           download statements for --start-year..--end-year and daily yields
  export-requests write each statement + its prompt for an external LLM run
  import          import a recorded outcome-blind annotation file (--annotations)
  extract         classify via the Anthropic API (needs ANTHROPIC_API_KEY)
  score           compute index + change per statement
  analyze         descriptive same-day 2y yield association

  synthetic       planted-signal unit test of the code path; NOT a market result

Examples:
  python main.py --mode fetch --db output/fomc_real.db
  python main.py --mode export-requests --db output/fomc_real.db --requests output/requests.json
  python main.py --mode import --db output/fomc_real.db --annotations output/annotations.json
  python main.py --mode score --db output/fomc_real.db
  python main.py --mode analyze --db output/fomc_real.db --label real
"""

import argparse
import json
from pathlib import Path

import analyze
import data_sources
import db
import extract


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", required=True,
                    choices=["fetch", "export-requests", "import", "extract",
                             "score", "analyze", "synthetic"])
    ap.add_argument("--db", default=db.DEFAULT_DB, help="SQLite file path")
    ap.add_argument("--source", default="FOMC", choices=["FOMC", "SYNTHETIC"])
    ap.add_argument("--label", default=None, help="report label (default: source)")
    ap.add_argument("--start-year", type=int, default=2021)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument("--cache-dir", default=data_sources.DEFAULT_CACHE_DIR)
    ap.add_argument("--requests", default="output/extraction_requests.json")
    ap.add_argument("--annotations", help="recorded LLM annotation JSON for --mode import")
    ap.add_argument("--out", default=None, help="analysis report path (.md)")
    args = ap.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = db.connect(args.db)
    db.init_db(conn)

    if args.mode == "fetch":
        n_s, n_y = data_sources.fetch_real(
            conn, start_year=args.start_year, end_year=args.end_year,
            cache_dir=args.cache_dir)
        print(f"[fetch] {n_s} official FOMC statements, {n_y} daily yield dates.")

    elif args.mode == "export-requests":
        n = extract.export_requests(conn, args.requests)
        print(f"[export-requests] wrote {n} statements to {args.requests}")

    elif args.mode == "import":
        if not args.annotations:
            ap.error("--mode import requires --annotations")
        payload = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
        n, overrides = extract.import_extractions(conn, payload)
        print(f"[import] validated and stored {n} annotations; {len(overrides)} "
              "rate_action value(s) replaced by the document-derived value:")
        for o in overrides:
            print(f"         {o['date']}: model={o['model']} -> derived={o['derived']}")

    elif args.mode == "extract":
        n = extract.extract_all(conn)
        print(f"[extract] extracted {n} statements (cached; re-runs are free).")

    elif args.mode == "score":
        n = analyze.score_all(conn, source=args.source)
        print(f"[score] scored {n} {args.source} statements.")

    elif args.mode == "analyze":
        label = args.label or args.source.lower()
        out_path = args.out or f"output/results_{label}.md"
        res = analyze.run_alignment(conn, source=args.source, label=label,
                                    out_path=out_path)
        _print_alignment(res, out_path)

    elif args.mode == "synthetic":
        n = data_sources.generate(conn, n=60, seed=7)
        analyze.score_all(conn, source="SYNTHETIC")
        out_path = args.out or "output/results_synthetic.md"
        res = analyze.run_alignment(conn, source="SYNTHETIC",
                                    label="synthetic", out_path=out_path)
        print(f"[synthetic] {n} fabricated statements; planted signal. "
              "This tests the code path only, not any market relationship.")
        _print_alignment(res, out_path)

    conn.close()


def _print_alignment(res, path):
    print(f"[analyze] events with a same-day yield window: {res['n_matched']}")
    print(f"          LEVEL  vs same-day 2y move : rho={_f(res['level_rho'])}")
    print(f"          CHANGE vs same-day 2y move : rho={_f(res['delta_rho'])}"
          f"  (p={_f(res.get('delta_p'))})")
    print(f"          sign agreement (change)    : {_f(res.get('sign_agreement'))}"
          f"  (n={res.get('n_sign', 0)})")
    print(f"          full report written to {path}")


def _f(x):
    return "n/a" if x is None or x != x else f"{x:.3f}"


if __name__ == "__main__":
    main()
