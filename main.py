"""
main.py
-------

One entry point, picked with --mode. Wires together:
  scoring.py       schema (enums/weights) + index/diff logic (deterministic)
  data_sources.py  offline sample loader + online scraper + synthetic generator
  db.py            SQLite storage
  extract.py       the one LLM call (extraction only, cached)
  analyze.py       scoring driver + level/change-vs-yield alignment

Pipeline stages, in order:

  fetch     load statement text into the DB
            (offline: the bundled real sample; --online: scrape the full set)
  extract   run the LLM on real statements -> cached structured records
            (needs ANTHROPIC_API_KEY; synthetic statements skip this)
  score     compute each statement's index + change vs the prior one
  analyze   compare the signal to 2y yield moves; write results_<label>.md

Convenience modes:
  synthetic   build a labelled fake dataset (with a planted signal) and run
              score + analyze on it. Needs no key and no network -- this is the
              method-check, and what runs in the build sandbox.
  all         real pipeline end to end: fetch (sample) -> extract -> score ->
              analyze. Needs an API key.

Examples:
  python main.py --mode synthetic
  python main.py --mode fetch                 # load bundled real sample
  python main.py --mode fetch --online        # scrape full set (local only)
  export ANTHROPIC_API_KEY=...; python main.py --mode all
"""

import argparse

import db
import data_sources
import extract
import analyze


def main():
    ap = argparse.ArgumentParser(description="FOMC hawkish/dovish signal pipeline")
    ap.add_argument("--mode", required=True,
                    choices=["fetch", "extract", "score", "analyze",
                             "synthetic", "all"])
    ap.add_argument("--db", default=db.DEFAULT_DB, help="SQLite file path")
    ap.add_argument("--source", default="FOMC",
                    help="which statements to score/analyze (FOMC or SYNTHETIC)")
    ap.add_argument("--online", action="store_true",
                    help="for --mode fetch: scrape live sources instead of the sample")
    args = ap.parse_args()

    conn = db.connect(args.db)
    db.init_db(conn)

    if args.mode == "fetch":
        if args.online:
            n_s, n_y = data_sources.fetch_real(conn)
            print(f"[fetch] scraped {n_s} statements and {n_y} yield days (live).")
        else:
            n = data_sources.load_sample(conn)
            print(f"[fetch] loaded {n} real FOMC statements from the bundled sample.")

    elif args.mode == "extract":
        n = extract.extract_all(conn, source="FOMC")
        print(f"[extract] extracted {n} statements (cached; re-runs are free).")

    elif args.mode == "score":
        n = analyze.score_all(conn, source=args.source)
        print(f"[score] scored {n} {args.source} statements.")

    elif args.mode == "analyze":
        out_path = f"results_{args.source.lower()}.md"
        res = analyze.run_alignment(conn, source=args.source,
                                    label=args.source.lower(), out_path=out_path)
        _print_alignment(res, out_path)

    elif args.mode == "synthetic":
        n = data_sources.generate(conn, n=60, seed=7)
        analyze.score_all(conn, source="SYNTHETIC")
        res = analyze.run_alignment(conn, source="SYNTHETIC",
                                    label="synthetic", out_path="results_synthetic.md")
        print(f"[synthetic] generated + scored {n} statements (signal planted).")
        _print_alignment(res, "results_synthetic.md")

    elif args.mode == "all":
        n = data_sources.load_sample(conn)
        print(f"[all] loaded {n} real statements.")
        m = extract.extract_all(conn, source="FOMC")
        print(f"[all] extracted {m} statements.")
        s = analyze.score_all(conn, source="FOMC")
        print(f"[all] scored {s} statements.")
        res = analyze.run_alignment(conn, source="FOMC",
                                    label="real", out_path="results_real.md")
        _print_alignment(res, "results_real.md")

    conn.close()


def _print_alignment(res, path):
    print(f"[analyze] statements with a usable yield window: {res['n_matched']}")
    print(f"          LEVEL  vs same-day 2y move : rho={_f(res['level_rho'])}")
    print(f"          CHANGE vs same-day 2y move : rho={_f(res['delta_rho'])}")
    print(f"          sign agreement (change)    : {_f(res.get('sign_agreement'))}"
          f"  (n={res.get('n_sign', 0)})")
    print(f"          full report written to {path}")


def _f(x):
    return "n/a" if x != x else f"{x:.3f}"


if __name__ == "__main__":
    main()
