"""
extract.py
==========

The ONE place the LLM is used, and it does ONE thing: read a policy statement and
fill in the fixed schema from schema.py. It does not score, predict, or opine on
markets. I picked extraction because it is what the model is reliable at and what
I can actually check (every field comes with a short supporting quote).

Reliability tricks used here:
  * temperature 0 for the most repeatable output I can get.
  * a strict prompt that lists the exact allowed categories and asks for JSON only.
  * validate_record() rejects anything off-menu, so a bad response fails loudly.
  * every result is cached in SQLite under a key derived from
    (model + schema version + the exact statement text). Same input -> same row,
    so I pay the API once and every later run is free and identical.

If ANTHROPIC_API_KEY is not set and the answer isn't already cached, this raises
a clear error telling me to set the key or use --mode synthetic. I never silently
fabricate an extraction.
"""

import os
import json
import hashlib
import datetime
import urllib.request
import urllib.error

from scoring import SCORED_FIELDS, ORDINALS, SCHEMA_VERSION, validate_record
import db

# Default model. Swap to a cheaper one (e.g. a Haiku tier) by changing this one
# line -- the task is simple classification so a small model is usually fine.
MODEL = "claude-sonnet-4-6"
API_URL = "https://api.anthropic.com/v1/messages"


def _cache_key(text):
    """A content hash so identical inputs reuse the same cached extraction.
    Schema version is included so a schema change invalidates old answers."""
    h = hashlib.sha256()
    h.update(f"{MODEL}::{SCHEMA_VERSION}::".encode())
    h.update(text.encode())
    return h.hexdigest()


def build_prompt(statement_text):
    """Construct the extraction instructions straight from the schema, so the
    prompt and the scoring can never drift apart."""
    menu_lines = []
    for field in SCORED_FIELDS:
        cats = list(ORDINALS[field].keys())
        menu_lines.append(f"  - {field}: one of {cats}")
    menu = "\n".join(menu_lines)

    return f"""You are reading a central-bank (FOMC) policy statement and classifying its language.
Do NOT predict markets or give opinions. Only classify what the text says.

For each field, choose EXACTLY ONE value from the allowed list:
{menu}
  - dissent_count: integer, how many committee members voted against the action (0 if none)
  - quotes: an object mapping each of the fields above to a SHORT (<= 15 word) quote
    from the statement that justifies your choice (use "" if nothing applies)

Guidance:
  - inflation_stance / growth_labor_stance describe how the statement characterises
    current conditions, not your forecast.
  - rate_action is the decision THIS meeting (hold if unchanged).
  - balance_sheet: 'expanding' = active asset purchases/QE, 'runoff' = reducing holdings/QT,
    'neutral' = no change or not mentioned.
  - forward_guidance is what the statement signals about FUTURE moves.
  - dissent_direction is the net lean of any dissents (a dissenter wanting MORE
    tightening => 'hawkish'; wanting easing => 'dovish'; no dissent => 'none').

Return ONLY a JSON object with these keys and nothing else (no markdown, no prose).

STATEMENT:
\"\"\"
{statement_text}
\"\"\"
"""


def _parse_json(raw):
    """Pull a JSON object out of the model's text, tolerating stray fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # drop a leading ```json / ``` fence and the trailing ```
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[len("json"):]
    cleaned = cleaned.strip().strip("`").strip()
    # As a fallback, grab from the first { to the last }.
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)


def _call_claude(prompt):
    """Minimal HTTP call to the Anthropic Messages API using only the stdlib.

    I keep this dependency-free (no SDK) so there's nothing to explain that I
    didn't write. Reads the key from the environment; never hard-coded.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Either export it, or run with "
            "--mode synthetic to exercise the pipeline without any API calls."
        )
    body = json.dumps({
        "model": MODEL,
        "max_tokens": 1024,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode())
    # The response content is a list of blocks; concatenate the text ones.
    return "".join(b.get("text", "") for b in payload.get("content", []))


def extract_statement(conn, statement_row):
    """Extract one statement, using the cache if we've seen this exact text.

    Returns the validated record dict. Raises on an unparseable or off-menu
    response (I would rather fail than store a guess)."""
    text = statement_row["text"]
    key = _cache_key(text)

    cached = db.get_cached_extraction(conn, statement_row["id"], key)
    if cached is not None:
        return cached  # free + identical to last time

    raw = _call_claude(build_prompt(text))
    record = _parse_json(raw)
    validate_record(record)  # loud failure if the model went off-menu

    db.save_extraction(
        conn,
        statement_id=statement_row["id"],
        cache_key=key,
        model=MODEL,
        schema_version=SCHEMA_VERSION,
        raw_response=raw,
        record=record,
        created_at=datetime.datetime.utcnow().isoformat(),
    )
    return record


def extract_all(conn, source="FOMC"):
    """Extract every statement of a given source. Synthetic statements already
    carry their record (no LLM needed), so this is only for real text."""
    done = 0
    for row in db.get_statements(conn, source=source):
        extract_statement(conn, row)
        done += 1
    return done
