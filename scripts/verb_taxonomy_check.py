"""CLI for the verb-taxonomy parser, invoked by the GitHub Action.

Reads a PR body from stdin (default) or from `--body-file <path>` and
emits a markdown-formatted summary of findings to stdout. Designed to be
piped directly into `gh pr comment --body-file -` by the workflow.

If there are no findings (no conflicts, no non-canonical verbs), the
script emits no output and exits 0. The workflow uses the empty output
to decide whether to post a comment at all.

Exit code is always 0 — the verb taxonomy check is a warning surface,
not a merge blocker (per #515 acceptance criteria). The workflow itself
is non-blocking: it posts a comment if findings exist, and that's it.

Usage:
    echo "$PR_BODY" | python scripts/verb_taxonomy_check.py
    python scripts/verb_taxonomy_check.py --body-file body.txt
"""
from __future__ import annotations

import argparse
import sys

from scripts.verb_taxonomy_parser import parse_pr_body, ParseResult


_COMMENT_HEADER = "## Verb taxonomy findings"

_OPT_OUT_FOOTER = (
    "_To opt out of this check on a specific PR, add the "
    "`skip-verb-taxonomy` label. The canonical verb list lives in "
    "[`.mex/context/verb-taxonomy.md`](../blob/main/.mex/context/verb-taxonomy.md)._"
)


def _read_body(args: argparse.Namespace) -> str:
    """Read the PR body from --body-file or stdin."""
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    return sys.stdin.read()


def format_comment(result: ParseResult) -> str:
    """Format a ParseResult as a markdown PR comment.

    Returns the empty string if there are no findings (no conflicts and
    no non-canonical verbs), so the caller can decide not to post.
    """
    if not result.conflicts and not result.non_canonical:
        return ""

    parts: list[str] = [_COMMENT_HEADER, ""]
    parts.append(
        "This PR's body has the following issues with the canonical "
        "verb taxonomy:"
    )
    parts.append("")

    if result.conflicts:
        parts.append("### Conflicts (must fix)")
        parts.append("")
        for c in result.conflicts:
            parts.append(f"- {c}")
        parts.append("")

    if result.non_canonical:
        parts.append("### Non-canonical verbs (please review)")
        parts.append("")
        for w in result.non_canonical:
            parts.append(f"- {w}")
        parts.append("")

    parts.append(_OPT_OUT_FOOTER)
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verb-taxonomy check for PR bodies. Reads body from stdin "
            "(default) or --body-file. Emits a markdown comment to stdout "
            "if there are findings, or nothing if clean."
        ),
    )
    parser.add_argument(
        "--body-file",
        default=None,
        help="Read PR body from this file instead of stdin.",
    )
    args = parser.parse_args(argv)

    body = _read_body(args)
    result = parse_pr_body(body)
    comment = format_comment(result)

    if comment:
        sys.stdout.write(comment)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
