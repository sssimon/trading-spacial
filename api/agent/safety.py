"""Hallucination guard — Phase 5 of epic #400, §11.4.

Postcondition helper: given the assistant's final text and the list of
tool_result blocks that preceded it in the same conversation, return
every identifier the assistant mentioned that does NOT appear in any
tool_result. An empty return value means "fully grounded"; a non-empty
one is a hallucination smoke alarm.

Scope today: position IDs, tune IDs, and curated symbol tickers. These
are the three classes the propose_* tools take as inputs and that the
model is most prone to invent. Free-text dates, prices, USD amounts are
deliberately NOT checked — the model is allowed to compute / paraphrase
those from tool_result data (and false positives on those would teach
operators to ignore the guard).

This module is import-safe with no DB / network dependencies. It runs
as a CI invariant in tests/test_agent_hallucination.py. A future epic
may wire it into the loop as an optional production postcheck (refuse
to surface a turn whose grounding check fails — the user sees a fixed
"no pude verificar mi respuesta" message instead of the hallucinated
text). NOT wired today; Phase 5B is tests-only.

References:
  - pre-reg §11.4 "Hallucination guard"
  - btc_scanner.DEFAULT_SYMBOLS — the 10 curated tickers
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable

log = logging.getLogger("api.agent.safety")


# Curated symbol set. We mirror btc_scanner.DEFAULT_SYMBOLS here (10
# tickers) to avoid importing the scanner module from a test path.
# A test in test_agent_hallucination.py asserts the two stay in sync.
_CURATED_SYMBOLS_PAIRS = frozenset({
    "BTCUSDT", "ETHUSDT", "ADAUSDT", "AVAXUSDT", "DOGEUSDT",
    "UNIUSDT", "XLMUSDT", "PENDLEUSDT", "JUPUSDT", "RUNEUSDT",
})
# Base tickers (without USDT) — the assistant often says "BTC" not
# "BTCUSDT" in natural language. Both forms count as a symbol reference.
_CURATED_SYMBOLS_BASES = frozenset(
    s.replace("USDT", "") for s in _CURATED_SYMBOLS_PAIRS
)
ALL_CURATED_SYMBOL_TOKENS = _CURATED_SYMBOLS_PAIRS | _CURATED_SYMBOLS_BASES


# ── Reference extraction ──────────────────────────────────────────────


# "posición #42", "posición 42", "posicion 7" (no diacritics).
# `(?<!\d)` + `(?!\d)` avoid matching middles of longer numbers.
_POSITION_ID_RE = re.compile(
    r"(?i)\bposici[oó]n\s*#?\s*(?<!\d)(\d{1,9})(?!\d)"
)

# "tune #3", "propuesta de tune 7"
_TUNE_ID_RE = re.compile(
    r"(?i)\b(?:tune|propuesta)\s*(?:de\s*tune\s*)?#?\s*(?<!\d)(\d{1,9})(?!\d)"
)


def _extract_position_ids(text: str) -> set[int]:
    return {int(m.group(1)) for m in _POSITION_ID_RE.finditer(text)}


def _extract_tune_ids(text: str) -> set[int]:
    return {int(m.group(1)) for m in _TUNE_ID_RE.finditer(text)}


def _extract_symbol_tokens(text: str) -> set[str]:
    """Find any curated symbol token in `text` — both base ("BTC") and
    pair ("BTCUSDT") forms. Word-boundary matches only, so "BIT" doesn't
    falsely match "BTC"."""
    found: set[str] = set()
    for token in ALL_CURATED_SYMBOL_TOKENS:
        # \b around the token; case-insensitive for the assistant's
        # casual prose ("btc", "eth" lowercase happens) but we
        # normalize back to canonical uppercase in the returned set.
        if re.search(rf"\b{re.escape(token)}\b", text, flags=re.IGNORECASE):
            found.add(token.upper())
    return found


def extract_references(text: str) -> dict:
    """Return all extractable references in `text`. Used both on the
    assistant turn (the side being CHECKED) and on the tool_result
    payloads (the side that GROUNDS the check)."""
    return {
        "position_ids": _extract_position_ids(text),
        "tune_ids":     _extract_tune_ids(text),
        "symbols":      _extract_symbol_tokens(text),
    }


def _normalize_symbol(s: str) -> str:
    """Canonical form: BASE only. 'BTC' and 'BTCUSDT' collapse to 'BTC'."""
    s = s.upper()
    return s.replace("USDT", "") if s.endswith("USDT") else s


# ── Grounding scan ─────────────────────────────────────────────────────


def collect_grounding_from_tool_results(
    messages: list[dict],
) -> dict:
    """Scan a conversation's `messages` list and gather every reference
    surfaced inside a tool_result content block.

    The shape we care about (per pre-reg §6.1) is messages with role=
    'user' whose content is a list of {"type": "tool_result", ...}
    blocks. The tool_result's `content` is the JSON string the dispatch
    layer produced; we extract IDs + symbols from it the same way we
    extract from assistant text — using regex against the JSON's
    string form. Crude but: (a) it's deterministic, (b) tool handlers
    serialize structured data, (c) any ID/symbol the assistant could
    reference must appear LITERALLY in the JSON.
    """
    seen_position_ids: set[int] = set()
    seen_tune_ids:     set[int] = set()
    seen_symbols:      set[str] = set()

    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            raw = block.get("content")
            # Anthropic tool_result content can be a string OR a list of
            # text blocks. We coerce both shapes to a single haystack.
            if isinstance(raw, list):
                hay = " ".join(
                    (b.get("text") or "") for b in raw if isinstance(b, dict)
                )
            else:
                hay = raw or ""

            # Direct regex hits.
            refs = extract_references(hay)
            seen_position_ids.update(refs["position_ids"])
            seen_tune_ids.update(refs["tune_ids"])
            seen_symbols.update(_normalize_symbol(s) for s in refs["symbols"])

            # Belt-and-suspenders: most tool handlers return JSON objects
            # with explicit "id" and "symbol" / "tune_id" keys. Try to
            # parse the haystack as JSON and pull from those keys
            # specifically. If parse fails, regex above already covered
            # the literal-string case.
            try:
                parsed = json.loads(hay)
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                seen_position_ids.update(_walk_for_ids(parsed, key="id"))
                seen_position_ids.update(_walk_for_ids(parsed, key="position_id"))
                seen_tune_ids.update(_walk_for_ids(parsed, key="tune_id"))
                seen_symbols.update(_walk_for_symbols(parsed))

    return {
        "position_ids": seen_position_ids,
        "tune_ids":     seen_tune_ids,
        "symbols":      seen_symbols,
    }


def _walk_for_ids(obj, *, key: str) -> set[int]:
    """Recursively gather integer values at any node whose key matches."""
    out: set[int] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.add(int(v))
            else:
                out.update(_walk_for_ids(v, key=key))
    elif isinstance(obj, list):
        for item in obj:
            out.update(_walk_for_ids(item, key=key))
    return out


def _walk_for_symbols(obj) -> set[str]:
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "symbol" and isinstance(v, str):
                out.add(_normalize_symbol(v))
            else:
                out.update(_walk_for_symbols(v))
    elif isinstance(obj, list):
        for item in obj:
            out.update(_walk_for_symbols(item))
    return out


# ── Public API ─────────────────────────────────────────────────────────


class HallucinationDetected(AssertionError):
    """Raised by assert_text_grounded when the assistant's final text
    references an identifier that does not appear in any prior
    tool_result. Subclass of AssertionError so pytest treats it as a
    test failure with a clear traceback."""


def find_ungrounded_references(
    *,
    text: str,
    messages: list[dict],
) -> dict:
    """Return references in `text` that are NOT in the grounding set.

    Symbol comparison is normalized — "BTC" in the assistant text +
    "BTCUSDT" in a tool_result count as a match.

    Empty sets across the board = grounded. Any non-empty set = a
    hallucination smoke alarm.
    """
    asserted = extract_references(text)
    grounded = collect_grounding_from_tool_results(messages)
    return {
        "position_ids": asserted["position_ids"] - grounded["position_ids"],
        "tune_ids":     asserted["tune_ids"]     - grounded["tune_ids"],
        "symbols":      {_normalize_symbol(s) for s in asserted["symbols"]}
                          - grounded["symbols"],
    }


def assert_text_grounded(*, text: str, messages: list[dict]) -> None:
    """Raise HallucinationDetected if any reference in `text` was not
    grounded by a prior tool_result. Returns silently when grounded."""
    ungrounded = find_ungrounded_references(text=text, messages=messages)
    leaked = {k: sorted(v) for k, v in ungrounded.items() if v}
    if leaked:
        raise HallucinationDetected(
            f"assistant text references identifiers not grounded in any "
            f"prior tool_result: {leaked}"
        )
