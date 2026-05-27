---
name: verb-taxonomy
description: Canonical PR-body verb set (Closes, Advances, Narrows, Bounds, Refs) for referencing issues. Each verb has explicit semantics and GitHub-auto-close behavior. Sub-task C of #488; rung convención (Phase 1 — parser + tests provided; CI wiring is Phase 2).
triggers:
  - "Closes #"
  - "Advances #"
  - "Narrows #"
  - "Bounds #"
  - "Refs #"
  - "Tracks #"
  - "verb taxonomy"
  - "pr body"
edges:
  - target: context/ci-discipline.md
    condition: when a PR body's verb choice intersects with admin-merge or flake-list decisions
last_updated: 2026-05-26
---

# Verb taxonomy

## Why this file exists

GitHub recognizes one verb on PR bodies: `Closes #N` (and synonyms `Fixes`, `Resolves`). On merge, it auto-closes the issue.

This repo has issues that are **addressed but not closed** by a PR (the substantive work continues elsewhere, or the closure depends on a follow-up landing first). Voronov from PR #486 review: *"GitHub doesn't recognize 'Advances' — it's a custom convention. Anyone can write 'Closes' when they mean 'Advances'."*

Without a canonical taxonomy, three drift surfaces open:

1. **Typos that don't trigger auto-close.** A PR meaning to close an issue writes `Closeds #N` and the issue stays open silently.
2. **Verb conflicts on the same issue.** `Closes #N` and `Advances #N` in the same body. Ambiguous; one of them is wrong.
3. **Unauthorized verbs.** Someone introduces `Resolves #N` or `Handles #N` — non-canonical, future readers don't know what semantics were intended.

This file is the canonical taxonomy that resolves the three. Rung convención (Phase 1). Promotion to rung test is sub-task C Phase 2 — a GitHub Action that runs the parser on PR open/edit. **Tracked in #515.**

## The verbs

| Verb | GitHub auto-action | Use when |
|---|---|---|
| `Closes #N` | auto-closes on merge | Issue's acceptance criteria are fully satisfied by this PR. No follow-up required. |
| `Advances #N` | none | Issue partially addressed. Substantive closure depends on later work (another PR, a separate decision, an out-of-scope refactor). Issue stays open. |
| `Narrows #N` | none | Issue's scope is reduced by this PR. What remains is genuinely smaller. Issue stays open but its acceptance criteria can be tightened. |
| `Bounds #N` | none | Issue's surface is constrained (defense-in-depth that doesn't fully prevent the failure mode but limits its damage). Issue stays open. |
| `Refs #N` | none | Issue mentioned for context only. No commitment that this PR addresses it. |
| `Tracks #N` | none | Same as `Refs`, slightly stronger ("we are aware of this and it's relevant to the design choice"). Functionally identical to `Refs` for tooling. |

GitHub also accepts: `Fixes #N`, `Resolves #N`, `Close #N`, `Fix #N`, `Resolve #N`, plus past-tense variants. **In this repo, use only `Closes #N`** for the auto-close behavior. Other GitHub-recognized synonyms are valid but discouraged for consistency.

## Conflict rules

A PR body must NOT have multiple verb directives on the same issue.

**Invalid:**
```
Closes #481
Advances #481
```
Ambiguous: does the merge close #481 or leave it open? Pick one.

**Valid (different issues):**
```
Closes #481
Advances #477
```
Each issue has exactly one verb. Clear.

**Valid (same issue, different rungs):**
```
Closes #481 — message and runtime now agree.
Refs #481 — historical context.
```
This is OK because `Refs` is informational; only ONE actionable verb (`Closes`) applies. But the parser will flag it as a duplicate-issue mention; resolve by removing the `Refs` line.

## How the parser detects drift

The parser (`tests/test_verb_taxonomy_parser.py`) extracts patterns matching `<Verb> #<number>` from a PR body and builds a mapping `{issue_number: list[verb]}`. It asserts:

1. **Every verb is in the canonical set.** Non-canonical verbs (`Resolves`, `Handles`, `Closeds`) fail with a named typo or unknown-verb error.
2. **Each issue has at most one actionable verb.** `Refs` and `Tracks` are non-actionable and don't count toward the limit. `Closes`, `Advances`, `Narrows`, `Bounds` are actionable; only one per issue.

The parser is pure Python with no API calls. It can be invoked locally before opening a PR (`python -m pytest tests/test_verb_taxonomy_parser.py`) or wired into a GitHub Action in Phase 2.

## Examples from this repo's history

### PR #486 (Bundle 1)

```
- Closes #481 — message and runtime now agree
- Advances #477 — Path 3 (honest narrowing) — substantive close pending scaffold-promotion
- Tracks #487 — sentinel-importable surface
```

Three issues, three different verbs, no conflicts. Canonical.

### PR #496 (Bundle 4 robustness sweep)

```
Closes #474 #476 #480.
```

Three issues, one verb each (the `Closes` distributes). Canonical. After Voronov review, this PR's framing was demoted from "one invariant" to "three predicates co-located"; the verb taxonomy itself was fine.

### Hypothetical drift (would fail the parser)

```
Closes #500
Closes #500 by virtue of the migration landing
```

Same issue (`#500`) with two `Closes` directives. Parser flags as duplicate actionable verb. Resolve by removing the redundant line.

## Promotion to rung test (Phase 2 — open)

The parser library is in place (Phase 1). The next step is a GitHub Action `.github/workflows/verb-taxonomy.yml` that:

1. Triggers on `pull_request` events (`opened`, `edited`, `synchronize`).
2. Runs the parser against `${{ github.event.pull_request.body }}`.
3. Posts a comment on the PR with the parsed taxonomy (so the author sees what GitHub will do on merge).
4. Fails the check if conflicts or non-canonical verbs are found.

Implementation is its own work; tracked in **#515** (#488 sub-task C Phase 2). Until then, the parser exists as a pytest helper that can be invoked manually.

## Related

- `.mex/context/ci-discipline.md` — admin-merge policy (sub-task E). The verb taxonomy and the admin-merge discipline are sibling concerns: both convert reviewer-attention discipline into auditable artifacts.
- `.mex/context/conventions.md` — the registry. The `Issue cerrado` column there parses as `Closes` semantics by default; this file extends the vocabulary for cases the registry's `Issue cerrado` column flags as "advanced" or partial.
