"""Verb-taxonomy parser for PR body verb directives.

The parser extracts `<Verb> #<number>` directives from a PR body and
classifies each verb against the canonical taxonomy documented in
`.mex/context/verb-taxonomy.md`:

  - Actionable: Closes, Advances, Narrows, Bounds
  - Non-actionable: Refs, Tracks
  - GitHub synonyms for Closes (Fixes, Resolves, ...): accepted but
    flagged as non-canonical for this repo.
  - Anything else (typos, unknown verbs): flagged as non-canonical.

The parser also detects conflicts: the same issue referenced by more
than one actionable verb in the same body (ambiguous merge intent).

Pure Python, no I/O, no API calls. Importable from tests and from the
CI workflow (via `scripts/verb_taxonomy_check.py`).

Originally lived inline in `tests/test_verb_taxonomy_parser.py` and was
extracted to a module so the GitHub Action `.github/workflows/verb-
taxonomy.yml` (sub-task C Phase 2 of #488) can invoke it without
importing from `tests/`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# --- Taxonomy ---

CANONICAL_ACTIONABLE_VERBS: frozenset[str] = frozenset({
    "Closes", "Advances", "Narrows", "Bounds",
})

CANONICAL_NON_ACTIONABLE_VERBS: frozenset[str] = frozenset({
    "Refs", "Tracks",
})

CANONICAL_VERBS: frozenset[str] = (
    CANONICAL_ACTIONABLE_VERBS | CANONICAL_NON_ACTIONABLE_VERBS
)

# GitHub-recognized synonyms for Closes. Accepted by the parser as
# semantically-Closes but flagged as non-canonical for this repo.
GITHUB_CLOSES_SYNONYMS: frozenset[str] = frozenset({
    "Fixes", "Resolves", "Close", "Fix", "Resolve",
    "Closed", "Fixed", "Resolved",
})


# --- Data types ---

@dataclass(frozen=True)
class VerbDirective:
    """A parsed `<Verb> #<number>` directive from a PR body."""
    verb: str
    issue: int
    line_number: int  # 1-indexed
    is_actionable: bool


@dataclass(frozen=True)
class ParseResult:
    """The output of parsing a PR body."""
    directives: tuple[VerbDirective, ...]
    conflicts: tuple[str, ...]      # human-readable conflict descriptions
    non_canonical: tuple[str, ...]  # human-readable warnings


# --- Parser ---

# Match: word starting with capital letter, followed by space, then `#NNN`.
# Capture verb (group 1) and issue number (group 2). We capture the verb
# loosely (any capitalized word) so the parser can detect typos and
# unauthorized verbs by name, rather than missing them entirely.
_DIRECTIVE_PATTERN = re.compile(
    r"\b([A-Z][a-z]+)\s+#(\d+)\b",
)


def parse_pr_body(body: str) -> ParseResult:
    """Parse a PR body and return all verb directives + conflicts.

    The parser is permissive on verb capture (any capitalized word
    followed by `#N` matches) so that typos and unauthorized verbs are
    detected by name rather than silently ignored.
    """
    if not body:
        return ParseResult(directives=(), conflicts=(), non_canonical=())

    lines = body.split("\n")
    directives: list[VerbDirective] = []
    non_canonical: list[str] = []

    for line_num, line in enumerate(lines, start=1):
        for match in _DIRECTIVE_PATTERN.finditer(line):
            verb = match.group(1)
            issue = int(match.group(2))

            if verb in CANONICAL_VERBS:
                directives.append(VerbDirective(
                    verb=verb,
                    issue=issue,
                    line_number=line_num,
                    is_actionable=verb in CANONICAL_ACTIONABLE_VERBS,
                ))
            elif verb in GITHUB_CLOSES_SYNONYMS:
                directives.append(VerbDirective(
                    verb=verb,
                    issue=issue,
                    line_number=line_num,
                    is_actionable=True,
                ))
                non_canonical.append(
                    f"line {line_num}: {verb!r} is a GitHub synonym for "
                    f"Closes but is non-canonical for this repo. Use "
                    f"Closes #{issue} instead for consistency."
                )
            else:
                # Possible typo or unknown verb. Flag.
                non_canonical.append(
                    f"line {line_num}: {verb!r} is not a recognized verb. "
                    f"Did you mean one of: "
                    f"{', '.join(sorted(CANONICAL_VERBS))}? "
                    f"(See .mex/context/verb-taxonomy.md.)"
                )

    # Detect conflicts: same issue with multiple actionable verbs.
    actionable_by_issue: dict[int, list[VerbDirective]] = {}
    for d in directives:
        if d.is_actionable:
            actionable_by_issue.setdefault(d.issue, []).append(d)

    conflicts: list[str] = []
    for issue, ds in actionable_by_issue.items():
        if len(ds) > 1:
            verbs_used = [(d.verb, d.line_number) for d in ds]
            conflicts.append(
                f"issue #{issue} has {len(ds)} actionable verb directives: "
                + ", ".join(f"{v} on line {ln}" for v, ln in verbs_used)
                + ". Pick exactly one. (See .mex/context/verb-taxonomy.md.)"
            )

    return ParseResult(
        directives=tuple(directives),
        conflicts=tuple(conflicts),
        non_canonical=tuple(non_canonical),
    )
