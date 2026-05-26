"""Verb-taxonomy parser + tests for PR body verb directives.

Promotes the verb taxonomy from rung *convención* (documented in
`.mex/context/verb-taxonomy.md`) to rung *test* (Phase 1 — parser is
unit-tested; Phase 2 wiring into a GitHub Action that runs on PR events
is tracked under #488 sub-task C).

The parser:
  - Extracts patterns matching `<Verb> #<number>` from a PR body.
  - Classifies each verb as actionable (`Closes`, `Advances`, `Narrows`,
    `Bounds`) or non-actionable (`Refs`, `Tracks`).
  - Detects conflicts: same issue with multiple actionable verbs.
  - Detects unknown verbs (typos like `Cloesd`, unauthorized verbs like
    `Resolves` -- the latter is GitHub-recognized but discouraged in
    this repo for consistency).

The parser is pure Python with no API calls. It can be invoked locally
before opening a PR, or wired to a CI action via Phase 2.

Closes #488 sub-task C (Phase 1).
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


# --- Tests ---

def test_parser_extracts_canonical_actionable_verbs():
    """Each of the four actionable verbs is recognized and classified."""
    body = (
        "Closes #100\n"
        "Advances #200\n"
        "Narrows #300\n"
        "Bounds #400\n"
    )
    result = parse_pr_body(body)
    assert len(result.directives) == 4
    by_issue = {d.issue: d for d in result.directives}
    assert by_issue[100].verb == "Closes"
    assert by_issue[100].is_actionable is True
    assert by_issue[200].verb == "Advances"
    assert by_issue[200].is_actionable is True
    assert by_issue[300].verb == "Narrows"
    assert by_issue[300].is_actionable is True
    assert by_issue[400].verb == "Bounds"
    assert by_issue[400].is_actionable is True
    assert result.conflicts == ()
    assert result.non_canonical == ()


def test_parser_extracts_non_actionable_verbs():
    """Refs and Tracks are recognized but classified as non-actionable."""
    body = "Refs #100\nTracks #200"
    result = parse_pr_body(body)
    assert len(result.directives) == 2
    by_issue = {d.issue: d for d in result.directives}
    assert by_issue[100].verb == "Refs"
    assert by_issue[100].is_actionable is False
    assert by_issue[200].verb == "Tracks"
    assert by_issue[200].is_actionable is False
    assert result.conflicts == ()
    assert result.non_canonical == ()


def test_parser_flags_conflict_on_same_issue_multiple_actionable_verbs():
    """Closes #500 + Advances #500 in the same body is a conflict."""
    body = (
        "Closes #500 by adding the CHECK constraint.\n"
        "Advances #500 because the rung is now schema, not convención.\n"
    )
    result = parse_pr_body(body)
    assert len(result.conflicts) == 1
    assert "#500" in result.conflicts[0]
    assert "2 actionable verb directives" in result.conflicts[0]
    assert "Closes" in result.conflicts[0]
    assert "Advances" in result.conflicts[0]


def test_parser_allows_same_issue_with_actionable_plus_refs():
    """Closes #N and Refs #N in the same body is NOT a conflict — Refs is
    non-actionable, only one ACTIONABLE verb applies."""
    body = (
        "Closes #500 by the migration.\n"
        "Refs #500 for the spec history.\n"
    )
    result = parse_pr_body(body)
    assert result.conflicts == (), (
        f"expected no conflicts; got: {result.conflicts}"
    )
    assert len(result.directives) == 2


def test_parser_flags_github_synonyms_as_non_canonical():
    """Fixes / Resolves are GitHub-recognized but discouraged. The parser
    treats them as Closes-equivalent (actionable) but flags as non-canonical."""
    body = "Fixes #100\nResolves #200"
    result = parse_pr_body(body)
    assert len(result.directives) == 2
    assert all(d.is_actionable for d in result.directives)
    assert len(result.non_canonical) == 2
    assert "Fixes" in result.non_canonical[0]
    assert "Resolves" in result.non_canonical[1]


def test_parser_flags_typos_as_non_canonical():
    """A typo like `Cloesd #N` is captured by the loose verb regex and
    flagged as non-canonical with a suggestion of the canonical set."""
    body = "Cloesd #100 by the fix.\n"
    result = parse_pr_body(body)
    # Cloesd is matched as a verb pattern but not classified as a directive,
    # since it isn't in any canonical set.
    assert result.directives == ()
    assert len(result.non_canonical) == 1
    assert "Cloesd" in result.non_canonical[0]


def test_parser_flags_unauthorized_verbs():
    """A verb like Handles #N or Addresses #N is non-canonical."""
    body = "Handles #100 by adding validation.\n"
    result = parse_pr_body(body)
    assert result.directives == ()
    assert len(result.non_canonical) == 1
    assert "Handles" in result.non_canonical[0]


def test_parser_handles_empty_or_no_directives():
    """A body with no verb directives parses cleanly to empty results."""
    body = "Just a description without any verb #N patterns.\n"
    # Without a capitalized word followed by #N, no directives.
    result = parse_pr_body(body)
    assert result.directives == ()
    assert result.conflicts == ()
    assert result.non_canonical == ()


def test_parser_handles_none_or_empty_body():
    """Empty body / None body returns empty result without crashing."""
    assert parse_pr_body("").directives == ()
    assert parse_pr_body("").conflicts == ()


def test_parser_handles_real_pr_486_body_excerpt():
    """The actual body of PR #486 (Bundle 1) parses cleanly."""
    body = (
        "## Closes / Refs\n"
        "\n"
        "- Closes #481 -- message and runtime now agree\n"
        "- Advances #477 -- Path 3 selected; substantive registry-row "
        "alignment depends on scaffold PR\n"
        "- Tracks #487 -- sentinel-importable surface; wider asymmetry "
        "beyond #477's factory framing\n"
    )
    result = parse_pr_body(body)
    assert result.conflicts == ()
    assert result.non_canonical == ()
    by_issue = {d.issue: d for d in result.directives}
    assert by_issue[481].verb == "Closes"
    assert by_issue[481].is_actionable is True
    assert by_issue[477].verb == "Advances"
    assert by_issue[477].is_actionable is True
    assert by_issue[487].verb == "Tracks"
    assert by_issue[487].is_actionable is False


def test_parser_handles_real_pr_496_body_excerpt():
    """The actual body of PR #496 (Bundle 4): single-line `Closes #N #M #L`
    distributes the verb across all three issues."""
    body = (
        "## Closes / Refs\n"
        "\n"
        "- Closes #474 -- bulk quarantine safety\n"
        "- Closes #476 -- idempotency probe anchored\n"
        "- Closes #480 -- orphan positions_new recovery\n"
    )
    result = parse_pr_body(body)
    assert result.conflicts == ()
    assert len(result.directives) == 3
    assert all(d.verb == "Closes" for d in result.directives)
    issues = {d.issue for d in result.directives}
    assert issues == {474, 476, 480}


def test_canonical_verb_sets_do_not_overlap():
    """The actionable and non-actionable sets must be disjoint."""
    overlap = CANONICAL_ACTIONABLE_VERBS & CANONICAL_NON_ACTIONABLE_VERBS
    assert overlap == set(), f"actionable and non-actionable overlap: {overlap}"


def test_github_synonyms_disjoint_from_canonical():
    """GitHub synonyms must not collide with the canonical set."""
    overlap = GITHUB_CLOSES_SYNONYMS & CANONICAL_VERBS
    assert overlap == set(), f"synonyms overlap canonical verbs: {overlap}"
