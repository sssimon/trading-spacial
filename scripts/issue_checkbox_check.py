"""Issue checkbox-completion check for merged PRs.

Reads a PR body from stdin, extracts every `Closes #N` directive (and its
GitHub-recognized synonyms — Fixes/Resolves/etc.), fetches each named
issue's body via `gh issue view`, parses Markdown checkbox list items
(`- [ ]` vs `- [x]`), and emits a Markdown-formatted PR comment to stdout
if any closed issue has unchecked acceptance criteria.

The check is non-blocking by design — at merge time, the action has
already happened. The comment is a warning surface, not a gate. Per
#515 acceptance criteria: "If not all checked: warn (don't block — the
merger may have verified offline)."

If there are no findings (every issue closed by this PR has all
checkboxes ticked, or the PR closes no issues) the script emits no
output and exits 0.

Usage (inside GitHub Action):

    cat <<<"$PR_BODY" | python -m scripts.issue_checkbox_check \
        --repo "$GITHUB_REPOSITORY" > comment.md

Requires `gh` CLI on PATH with a valid GH_TOKEN in env (both standard on
GitHub-hosted runners).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass

from scripts.verb_taxonomy_parser import (
    GITHUB_CLOSES_SYNONYMS,
    parse_pr_body,
)


# GitHub auto-closes on these verbs (Closes + every synonym the parser
# already recognises). The checkbox check applies to all of them.
_CLOSING_VERBS: frozenset[str] = frozenset({"Closes"}) | GITHUB_CLOSES_SYNONYMS

# Matches a Markdown task-list item:
#   - [ ] label
#   - [x] label
#   * [X] label
# The first capture group is the state character (' ', 'x', 'X'); the
# second is the label text. Use `[ \t]` rather than `\s` so a missing
# label does not let the engine consume the trailing newline and merge
# with the next bullet.
_CHECKBOX_PATTERN = re.compile(
    r"^[ \t]*[-*][ \t]+\[([ xX])\][ \t]*(.*)$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class IssueFinding:
    issue: int
    checked: tuple[str, ...]
    unchecked: tuple[str, ...]


def parse_checkboxes(body: str) -> tuple[list[str], list[str]]:
    """Return ``(checked, unchecked)`` checkbox labels from an issue body.

    Order is preserved per group. Empty labels are kept (they reflect
    how the issue was written and the warning's job is to mirror that).
    """
    checked: list[str] = []
    unchecked: list[str] = []
    if not body:
        return checked, unchecked
    for match in _CHECKBOX_PATTERN.finditer(body):
        state = match.group(1)
        label = match.group(2).strip()
        if state in ("x", "X"):
            checked.append(label)
        else:
            unchecked.append(label)
    return checked, unchecked


def fetch_issue_body(issue_number: int, repo: str) -> str | None:
    """Fetch the body of one issue via ``gh issue view``.

    Returns ``None`` if the fetch fails for any reason (issue missing,
    network error, auth missing). The caller should treat ``None`` as
    "skip this issue" rather than a fatal failure.
    """
    try:
        result = subprocess.run(
            [
                "gh", "issue", "view", str(issue_number),
                "--repo", repo,
                "--json", "body",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    body = data.get("body")
    return body if isinstance(body, str) else None


def format_comment(findings: list[IssueFinding]) -> str:
    """Format incomplete-acceptance-criteria warnings as a Markdown comment.

    Returns the empty string if no findings, so the caller can decide
    not to post.
    """
    if not findings:
        return ""

    parts: list[str] = [
        "## Checkbox completion check (warning)",
        "",
        (
            "This PR closes one or more issues whose body contains "
            "checkbox-listed acceptance criteria that are **not all "
            "checked**:"
        ),
        "",
    ]
    for f in findings:
        parts.append(f"### Issue #{f.issue}")
        parts.append("")
        parts.append(f"**Unchecked ({len(f.unchecked)}):**")
        for label in f.unchecked:
            display = label if label else "_(no label)_"
            parts.append(f"  - [ ] {display}")
        if f.checked:
            parts.append("")
            parts.append(f"_({len(f.checked)} item(s) already checked.)_")
        parts.append("")
    parts.append(
        "_If the acceptance criteria were verified offline (manual "
        "testing, external review, etc.) this is just a reminder. The "
        "merge has already happened. If something was missed, follow up "
        "in a subsequent PR or by editing the issue checkboxes for "
        "accuracy. To opt out, add the `skip-verb-taxonomy` label._"
    )
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Issue checkbox-completion check for merged PRs. Reads PR "
            "body from stdin, fetches each Closes-referenced issue, and "
            "emits a Markdown comment to stdout if any issue has "
            "unchecked acceptance criteria."
        ),
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="GitHub repo in owner/name format (e.g. sssimon/trading-spacial).",
    )
    args = parser.parse_args(argv)

    body = sys.stdin.read()
    result = parse_pr_body(body)

    # Extract issue numbers from Closes/Fixes/Resolves directives.
    closing_issues = sorted({
        d.issue for d in result.directives
        if d.verb in _CLOSING_VERBS
    })

    findings: list[IssueFinding] = []
    for issue_number in closing_issues:
        issue_body = fetch_issue_body(issue_number, args.repo)
        if issue_body is None:
            # Skip issues we can't fetch — don't fail the whole check.
            continue
        checked, unchecked = parse_checkboxes(issue_body)
        if unchecked:
            findings.append(IssueFinding(
                issue=issue_number,
                checked=tuple(checked),
                unchecked=tuple(unchecked),
            ))

    comment = format_comment(findings)
    if comment:
        sys.stdout.write(comment)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
