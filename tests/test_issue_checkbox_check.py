"""Tests for `scripts/issue_checkbox_check`.

Covers the two pure-function units: checkbox parsing on issue bodies,
and Markdown comment formatting for the findings. The `gh`-invoking
fetch path is exercised at runtime in CI rather than mocked here — its
contract is small (subprocess returns stdout JSON) and the failure mode
is "return None and skip", which is intentionally non-fatal.
"""
from __future__ import annotations

from scripts.issue_checkbox_check import (
    IssueFinding,
    format_comment,
    parse_checkboxes,
)


# --- parse_checkboxes ---

def test_parse_checkboxes_separates_checked_from_unchecked():
    body = (
        "## Acceptance criteria\n"
        "\n"
        "- [ ] First criterion\n"
        "- [x] Second criterion\n"
        "- [ ] Third criterion\n"
    )
    checked, unchecked = parse_checkboxes(body)
    assert checked == ["Second criterion"]
    assert unchecked == ["First criterion", "Third criterion"]


def test_parse_checkboxes_handles_capital_X_as_checked():
    """GitHub renders both `[x]` and `[X]` as checked."""
    body = "- [X] Done\n- [x] Also done\n- [ ] Not done"
    checked, unchecked = parse_checkboxes(body)
    assert checked == ["Done", "Also done"]
    assert unchecked == ["Not done"]


def test_parse_checkboxes_handles_asterisk_bullets():
    """`* [ ]` is valid Markdown task-list syntax."""
    body = "* [ ] First\n* [x] Second"
    checked, unchecked = parse_checkboxes(body)
    assert checked == ["Second"]
    assert unchecked == ["First"]


def test_parse_checkboxes_ignores_non_checkbox_lines():
    """Plain text and headers must not be classified as checkboxes."""
    body = (
        "Some intro paragraph.\n"
        "\n"
        "## A header\n"
        "- A bullet that is not a checkbox\n"
        "- [ ] Actual checkbox\n"
    )
    checked, unchecked = parse_checkboxes(body)
    assert checked == []
    assert unchecked == ["Actual checkbox"]


def test_parse_checkboxes_handles_indented_checkboxes():
    """Nested task-list items are still detected."""
    body = "  - [ ] Indented unchecked\n    - [x] Deeper checked"
    checked, unchecked = parse_checkboxes(body)
    assert checked == ["Deeper checked"]
    assert unchecked == ["Indented unchecked"]


def test_parse_checkboxes_handles_empty_or_none_body():
    """Empty or missing body returns empty lists without crashing."""
    assert parse_checkboxes("") == ([], [])
    # Type-checker doesn't allow passing None, but the runtime guard
    # exists, so call it via Any to verify.
    assert parse_checkboxes(None) == ([], [])  # type: ignore[arg-type]


def test_parse_checkboxes_preserves_empty_labels():
    """An empty-label checkbox (`- [ ]` with nothing after) is captured."""
    body = "- [ ]\n- [x] With label"
    checked, unchecked = parse_checkboxes(body)
    assert checked == ["With label"]
    assert unchecked == [""]


# --- format_comment ---

def test_format_comment_returns_empty_string_when_no_findings():
    assert format_comment([]) == ""


def test_format_comment_renders_single_issue_finding():
    finding = IssueFinding(
        issue=515,
        checked=("Workflow runs on PR events",),
        unchecked=("Workflow posts comments", "Opt-out label"),
    )
    comment = format_comment([finding])
    assert "## Checkbox completion check (warning)" in comment
    assert "Issue #515" in comment
    assert "Unchecked (2)" in comment
    assert "- [ ] Workflow posts comments" in comment
    assert "- [ ] Opt-out label" in comment
    assert "1 item(s) already checked" in comment
    assert "skip-verb-taxonomy" in comment


def test_format_comment_renders_multiple_issues():
    findings = [
        IssueFinding(
            issue=100,
            checked=(),
            unchecked=("First item",),
        ),
        IssueFinding(
            issue=200,
            checked=("Done",),
            unchecked=("Pending",),
        ),
    ]
    comment = format_comment(findings)
    assert "Issue #100" in comment
    assert "Issue #200" in comment
    assert "- [ ] First item" in comment
    assert "- [ ] Pending" in comment


def test_format_comment_handles_empty_label_in_unchecked():
    finding = IssueFinding(
        issue=999,
        checked=(),
        unchecked=("",),
    )
    comment = format_comment([finding])
    # An empty label should not collapse into a hyphen with no content;
    # the comment renders a placeholder so the line still reads.
    assert "_(no label)_" in comment


def test_format_comment_omits_checked_count_when_zero_checked():
    finding = IssueFinding(
        issue=42,
        checked=(),
        unchecked=("Single unchecked",),
    )
    comment = format_comment([finding])
    assert "already checked" not in comment
