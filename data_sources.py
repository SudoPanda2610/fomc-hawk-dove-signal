"""Official FOMC statements and FRED yields, plus a labelled synthetic check.

``fetch_real`` selects complete calendar years before reading yield outcomes.
It retains exact HTTP response bytes and a SHA256-verified source manifest in
``output/source_cache``; reruns use those bytes without a network connection.
Delete the cache explicitly to request a fresh source snapshot. The default
2021--2025 window contains all 40 scheduled statements, not a selected sample.
The bundled ``load_sample`` helper is retained for offline text demonstrations,
not as a substitute for real yield ingestion. ``generate`` is synthetic only.
"""

import json
import os
import re
import datetime
import random
import csv
import hashlib
import io
import math
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlencode

from scoring import SCORED_FIELDS, ORDINALS
from scoring import index_of, delta_index  # noqa: F401 (index_of used by callers)
import db

SAMPLE_PATH = os.path.join(os.path.dirname(__file__), "sample_statements.json")

CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
DEFAULT_CACHE_DIR = "output/source_cache"
MANIFEST_VERSION = 1
PARSER_SETTINGS = {
    "version": "fomc-fred-v1",
    "html_parser": "beautifulsoup4/html.parser",
    "statement_body": "#article .col-xs-12.col-sm-8.col-md-8:not(.heading)",
    "text_normalization": "Unicode whitespace collapsed; paragraphs separated by two newlines",
    "yield_units": "percent",
    "yield_missing_values": ["", "."],
    "yield_padding_calendar_days": 10,
}


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


def _soup(html):
    from bs4 import BeautifulSoup
    if isinstance(html, bytes):
        html = html.decode("utf-8-sig")
    return BeautifulSoup(html, "html.parser")


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split())


def _statement_date(url):
    parsed = urlparse(url)
    match = re.fullmatch(r"/newsevents/pressreleases/monetary(\d{8})[a-z]\.htm",
                         parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "www.federalreserve.gov"
            or parsed.query or parsed.fragment or not match):
        raise ValueError(f"Not an official FOMC statement URL: {url}")
    return datetime.datetime.strptime(match.group(1), "%Y%m%d").date()


def parse_calendar(html, start_year, end_year):
    """Return chronological ``(ISO date, official URL)`` pairs for whole years.

    Require every meeting in each requested year to have a published statement.
    Missing historical years are an explicit error, never a reduced sample.
    """
    if (type(start_year) is not int or type(end_year) is not int
            or not 1900 <= start_year <= end_year <= 9998):
        raise ValueError("Select a valid inclusive calendar-year range")
    soup = _soup(html)
    panels = {}
    for panel in soup.select(".panel"):
        heading = panel.select_one(".panel-heading")
        match = re.fullmatch(r"(\d{4}) FOMC Meetings", _text(heading)) if heading else None
        if match:
            year = int(match.group(1))
            if year in panels:
                raise ValueError(f"Duplicate calendar panel for {year}")
            panels[year] = panel
    missing = sorted(set(range(start_year, end_year + 1)) - panels.keys())
    if missing:
        raise ValueError(f"Official calendar lacks requested years {missing}; "
                         f"available calendar years: {sorted(panels)}")
    selected = {}
    for year in range(start_year, end_year + 1):
        meetings = panels[year].select(".fomc-meeting")
        if not meetings:
            raise ValueError(f"Official calendar has no meetings for {year}")
        for meeting in meetings:
            # A notation vote only issues a strategy statement, not a policy decision.
            if "notation vote" in _text(meeting).lower():
                continue
            links = []
            for link in meeting.select("a[href]"):
                label = link.parent.find("strong")
                if (_text(link) == "HTML" and label is not None
                        and _text(label) == "Statement:"):
                    links.append(urljoin(CALENDAR_URL, link["href"]))
            if len(links) != 1:
                raise ValueError(f"Incomplete or ambiguous statement calendar for {year}: "
                                 f"{_text(meeting)}")
            url = links[0]
            date = _statement_date(url)
            month = meeting.select_one(".fomc-meeting__month")
            day = meeting.select_one(".fomc-meeting__date")
            days = re.findall(r"\d+", _text(day)) if day else []
            # Two-month meetings are labelled "Jan/Feb"; the statement is
            # released on the final day, so compare against the last month.
            last_month = _text(month).strip("*").split("/")[-1].strip() if month else ""
            if (date.year != year or not last_month or not days
                    or date.strftime("%b").lower() != last_month[:3].lower()
                    or date.day != int(days[-1])):
                raise ValueError(f"Statement URL disagrees with meeting date: {url}")
            iso = date.isoformat()
            if iso in selected:
                raise ValueError(f"Duplicate statement date in calendar: {iso}")
            selected[iso] = url
    return sorted(selected.items())


def parse_statement(html, expected_date):
    """Extract policy paragraphs and the complete vote, verifying release date."""
    expected = datetime.date.fromisoformat(expected_date)
    soup = _soup(html)
    article = soup.select_one("#article")
    if article is None:
        raise ValueError(f"Missing official statement article for {expected_date}")
    title = article.select_one("h3.title")
    published = article.select_one(".article__time")
    if title is None or _text(title) != "Federal Reserve issues FOMC statement":
        raise ValueError(f"Not an FOMC policy statement for {expected_date}")
    if (published is None
            or datetime.datetime.strptime(_text(published), "%B %d, %Y").date() != expected):
        raise ValueError(f"Statement release date disagrees with URL: {expected_date}")
    bodies = article.select(".col-xs-12.col-sm-8.col-md-8:not(.heading)")
    if len(bodies) != 1:
        raise ValueError(f"Missing or ambiguous statement body for {expected_date}")
    body = bodies[0]
    for element in body.select("script, style, nav, footer"):
        element.decompose()
    paragraphs = []
    has_vote = False
    for paragraph in body.find_all("p", recursive=False):
        text = _text(paragraph)
        if not text:
            continue
        if text.startswith(("For media inquiries", "Implementation Note")):
            break
        paragraphs.append(text)
        if text.startswith("Voting for the monetary policy action"):
            has_vote = True
    if not has_vote or len(paragraphs) < 2:
        raise ValueError(f"Incomplete policy text or voting paragraph for {expected_date}")
    return "\n\n".join(paragraphs)


def parse_fred_csv(content, series_id, start_date, end_date):
    """Parse percent yields; retain missing observations as ``None``."""
    if series_id not in ("DGS2", "DGS10"):
        raise ValueError(f"Unsupported yield series: {series_id}")
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    if start > end:
        raise ValueError("Yield start date follows end date")
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    rows = csv.reader(io.StringIO(content), strict=True)
    header = next(rows, None)
    if header not in (["observation_date", series_id], ["DATE", series_id]):
        raise ValueError(f"Unexpected FRED CSV header for {series_id}: {header!r}")
    out = []
    previous = None
    for row in rows:
        if len(row) != 2:
            raise ValueError(f"Malformed {series_id} CSV row: {row!r}")
        date = datetime.date.fromisoformat(row[0])
        if date.isoformat() != row[0] or (previous is not None and date <= previous):
            raise ValueError(f"Invalid, duplicate or unordered {series_id} date: {row[0]}")
        previous = date
        raw = row[1].strip()
        value = None if raw in ("", ".") else float(raw)
        if value is not None and not math.isfinite(value):
            raise ValueError(f"Nonfinite {series_id} yield on {row[0]}")
        if start <= date <= end:
            out.append((row[0], value))
    if not out or not any(value is not None for _, value in out):
        raise ValueError(f"No observed {series_id} yields in {start_date}--{end_date}")
    return out


class _SourceCache:
    """Raw HTTP responses indexed by URL; cached bytes must match the manifest."""

    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "manifest.json"
        if self.path.exists():
            self.manifest = json.loads(self.path.read_text(encoding="utf-8"))
            if (self.manifest.get("version") != MANIFEST_VERSION
                    or not isinstance(self.manifest.get("resources"), dict)):
                raise ValueError(f"Unsupported source cache manifest: {self.path}")
        else:
            self.manifest = {"version": MANIFEST_VERSION, "resources": {}}

    def save(self):
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.manifest, indent=2, allow_nan=False) + "\n",
                             encoding="utf-8")
        temporary.replace(self.path)

    def get(self, url):
        entry = self.manifest["resources"].get(url)
        filename = hashlib.sha256(url.encode("utf-8")).hexdigest() + ".raw"
        path = self.directory / filename
        if entry is not None:
            if entry.get("url") != url or entry.get("file") != filename:
                raise ValueError(f"Invalid cached source metadata for {url}")
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise ValueError(f"Missing cached source bytes: {path}") from exc
            if hashlib.sha256(content).hexdigest() != entry.get("sha256"):
                raise ValueError(f"Source cache SHA256 mismatch: {path}")
            return content
        import requests
        response = requests.get(url, timeout=90,
                                headers={"User-Agent": "FOMC-HawkDove academic research"})
        response.raise_for_status()
        if urlparse(response.url).netloc != urlparse(url).netloc:
            raise ValueError(f"Source redirected away from official host: {response.url}")
        content = response.content
        if not content:
            raise ValueError(f"Empty source response: {url}")
        path.write_bytes(content)
        self.manifest["resources"][url] = {
            "url": url,
            "response_url": response.url,
            "file": filename,
            "sha256": hashlib.sha256(content).hexdigest(),
            "fetched_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "content_type": response.headers.get("Content-Type"),
        }
        self.save()
        return content


def fetch_real(conn, start_year=2021, end_year=2025, cache_dir=DEFAULT_CACHE_DIR):
    """Load every statement in a preselected, complete calendar-year window.

    Return ``(statement_count, yield_day_count)``. The latter includes rows with
    missing yields, stored as SQL NULL. Calendar discovery and statement parsing
    finish before yield responses are read, and all source parsing finishes
    before database writes. Cached runs require no network.
    """
    if (type(start_year) is not int or type(end_year) is not int
            or not 1900 <= start_year <= end_year < datetime.date.today().year):
        raise ValueError("Real ingestion requires an ordered range of completed calendar years")
    cache = _SourceCache(cache_dir)
    selected = parse_calendar(cache.get(CALENDAR_URL), start_year, end_year)
    padding = datetime.timedelta(days=PARSER_SETTINGS["yield_padding_calendar_days"])
    start_date = (datetime.date(start_year, 1, 1) - padding).isoformat()
    end_date = (datetime.date(end_year, 12, 31) + padding).isoformat()
    cache.manifest["selection"] = {
        "source": "FOMC",
        "start_year": start_year,
        "end_year": end_year,
        "rule": "Every published policy statement in the selected complete calendar years",
        "selected_dates": [date for date, _ in selected],
        "statement_urls": [url for _, url in selected],
        "yield_start": start_date,
        "yield_end": end_date,
        "parser_settings": PARSER_SETTINGS,
    }
    cache.save()
    statements = [(date, url, parse_statement(cache.get(url), date))
                  for date, url in selected]
    series = {}
    for series_id in ("DGS2", "DGS10"):
        url = FRED_URL + "?" + urlencode(
            {"id": series_id, "cosd": start_date, "coed": end_date})
        series[series_id] = dict(parse_fred_csv(cache.get(url), series_id,
                                               start_date, end_date))
    all_dates = sorted(set(series["DGS2"]) | set(series["DGS10"]))
    for date, url, text in statements:
        db.upsert_statement(conn, source="FOMC", date=date, text=text,
                            title="Federal Reserve issues FOMC statement",
                            url=url, is_synthetic=False)
    for date in all_dates:
        db.upsert_yield(conn, date, series["DGS2"].get(date),
                        series["DGS10"].get(date), is_synthetic=False)
    cache.manifest["parsed_statements"] = [
        {"date": date, "url": url, "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        for date, url, text in statements
    ]
    cache.manifest["counts"] = {"statements": len(statements), "yield_days": len(all_dates)}
    cache.save()
    return len(statements), len(all_dates)


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
