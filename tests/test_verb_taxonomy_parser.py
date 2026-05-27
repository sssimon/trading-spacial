"""Tests for the verb-taxonomy parser (`scripts/verb_taxonomy_parser`).

Promotes the verb taxonomy from rung *convención* (documented in
`.mex/context/verb-taxonomy.md`) to rung *test* (Phase 1 — parser is
unit-tested; Phase 2 wires it into a GitHub Action that runs on PR
events, tracked under #515).

The parser itself lives in `scripts/verb_taxonomy_parser.py` so the
Phase 2 GitHub Action can import it without depending on `tests/`.
"""
from __future__ import annotations

from scripts.verb_taxonomy_parser import (
    CANONICAL_ACTIONABLE_VERBS,
    CANONICAL_NON_ACTIONABLE_VERBS,
    CANONICAL_VERBS,
    GITHUB_CLOSES_SYNONYMS,
    parse_pr_body,
)


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
