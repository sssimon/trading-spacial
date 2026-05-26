"""Registry-coherence test — promotes `.mex/context/conventions.md`'s
'Invariantes registradas' table from rung *convención* to rung *test*.

Background — Voronov 2026-05-26 strategic review:

> "Sub-task B is not 'promotion of the registry to rung test.' Sub-task B
> is the act that retroactively defines what every prior row meant. Until
> it lands, the registry's ontology is undecided. Rows added now are not
> 'convención-locked'; they are category-undefined."

This file is that promotion. Before it: the registry was a documentation
surface that claimed to be an enforcement surface. After it: the registry
fails CI when reality and notation diverge — at least for the categories
of reference this test verifies.

Phase 1 (this file): file paths and test function names mentioned in the
registry must resolve. If a row references `db/schema.py` and that file
is renamed, the test fails. If a row references
`test_error_message_does_not_overclaim_enforcement` and that test is
deleted or renamed, the test fails.

Phase 2 (future extensions, tracked in #488 sub-task B as it matures):
  - Schema CHECK fragment presence: spin up a fresh DB, init_db, parse
    sqlite_master.sql, assert CHECK fragments from the registry are
    present.
  - Python identifier resolution: classes, functions, module-level
    constants referenced in backticks resolve via importlib.
  - Cross-rung enum consistency (Pydantic Literal vs schema CHECK) is
    already tested in tests/test_cross_rung_consistency.py (#502).

Adding new categories to verify: add a new test function below following
the pattern. The extractor helpers are small and composable.

Closes #488 sub-task B (Phase 1).
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_FILE = REPO_ROOT / ".mex" / "context" / "conventions.md"


# --- Parser ---


def _read_registry_rows() -> list[dict[str, str]]:
    """Parse the 'Invariantes registradas' markdown table from conventions.md.

    Returns a list of dicts with keys: invariante, capa, mecanismo, issue
    -- one per row. Raises RuntimeError if the table cannot be located.
    """
    text = REGISTRY_FILE.read_text(encoding="utf-8")

    header_pattern = re.compile(
        r"^\|\s*Invariante de dominio\s*\|\s*Capa enforced\s*\|"
        r"\s*Mecanismo\s*\|\s*Issue cerrado\s*\|\s*$",
        re.MULTILINE,
    )
    match = header_pattern.search(text)
    if not match:
        raise RuntimeError(
            "could not find registry table header in "
            f"{REGISTRY_FILE} -- expected line: "
            "`| Invariante de dominio | Capa enforced | Mecanismo | Issue cerrado |`"
        )

    after_header = text[match.end():].lstrip("\n")
    lines = after_header.split("\n")

    if not lines or not lines[0].strip().startswith("|"):
        raise RuntimeError(
            "expected table divider line right after header; "
            f"got: {lines[0]!r}"
        )
    rows: list[dict[str, str]] = []
    for line in lines[1:]:
        stripped = line.strip()
        if not stripped or not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.split("|")[1:-1]]
        if len(cells) != 4:
            continue
        rows.append({
            "invariante": cells[0],
            "capa": cells[1],
            "mecanismo": cells[2],
            "issue": cells[3],
        })
    return rows


# --- Extractors ---


def _extract_file_paths(text: str) -> set[str]:
    """Extract repo-relative Python file paths from a markdown cell.

    Matches word-character segments separated by `/`, ending in `.py`.
    """
    pattern = re.compile(r"\b([\w]+(?:/[\w]+)+\.py)\b")
    return set(pattern.findall(text))


def _extract_test_names(text: str) -> set[str]:
    """Extract Python test function names (test_*) from a markdown cell.

    Excludes names followed by `.py` (those are file prefixes).
    """
    pattern = re.compile(r"\btest_[a-z_][\w]*\b(?!\.py)")
    return set(pattern.findall(text))


# --- Tests ---


def test_registry_table_parses_with_expected_shape():
    """The 'Invariantes registradas' table must be parseable: at least 5 rows,
    each with all four columns non-empty.

    Acts as the smoke test for the parser. If conventions.md's table format
    drifts (header changes, column reorder, table moves), this fails first."""
    rows = _read_registry_rows()
    assert len(rows) >= 5, (
        f"expected at least 5 registry rows (currently 14+); got {len(rows)}. "
        f"Did the table format change in .mex/context/conventions.md?"
    )
    for i, row in enumerate(rows):
        for col in ("invariante", "capa", "mecanismo", "issue"):
            assert row[col], (
                f"row {i} has empty {col!r} column: {row!r}"
            )


def test_registry_file_path_references_exist():
    """Every Python file path mentioned in a registry row's Mecanismo column
    must exist in the repo.

    Catches: file rename, file move, file deletion -- without registry update.
    """
    rows = _read_registry_rows()
    missing: list[tuple[int, str, str]] = []

    for i, row in enumerate(rows):
        paths = _extract_file_paths(row["mecanismo"])
        for path in paths:
            full = REPO_ROOT / path
            if not full.exists():
                missing.append((i, row["invariante"][:60], path))

    if missing:
        msg_lines = [
            "registry rows reference Python files that do not exist on disk:",
        ]
        for i, label, path in missing:
            msg_lines.append(f"  row {i} ({label!r}): {path}")
        msg_lines.append("")
        msg_lines.append(
            "If a file was renamed/moved, update the registry row in "
            ".mex/context/conventions.md to match. If a file was deleted, "
            "the row's invariant is likely also gone -- verify the closure "
            "and either remove the row or update it to its new mechanism."
        )
        assert not missing, "\n".join(msg_lines)


def test_registry_test_name_references_exist():
    """Every test function name (test_*) mentioned in a registry row's
    Mecanismo column must be defined somewhere under tests/.

    Catches: test rename, test deletion -- without registry update."""
    rows = _read_registry_rows()
    tests_dir = REPO_ROOT / "tests"

    test_def_pattern = re.compile(r"^\s*def (test_[a-z_][\w]*)\s*\(", re.MULTILINE)
    all_defined_tests: set[str] = set()
    for py_file in tests_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        all_defined_tests.update(test_def_pattern.findall(content))

    missing: list[tuple[int, str, str]] = []
    for i, row in enumerate(rows):
        test_names = _extract_test_names(row["mecanismo"])
        for name in test_names:
            if name not in all_defined_tests:
                missing.append((i, row["invariante"][:60], name))

    if missing:
        msg_lines = [
            "registry rows reference test functions that are not defined "
            "under tests/:",
        ]
        for i, label, name in missing:
            msg_lines.append(f"  row {i} ({label!r}): {name}")
        msg_lines.append("")
        msg_lines.append(
            "If a test was renamed, update the registry row in "
            ".mex/context/conventions.md to the new name. If a test was "
            "deleted, the row's claim that 'rung test enforces this' is "
            "now false."
        )
        assert not missing, "\n".join(msg_lines)


# --- Helper validation ---


def test_extractor_helpers_handle_canonical_inputs():
    """Smoke test for the extractors themselves."""
    sample = (
        "NewType in `db/transaction.py` -- mypy detects mis-use. "
        "See `tests/operators/test_ownership_validated_snapshot.py` for the "
        "`test_error_message_does_not_overclaim_enforcement` test."
    )

    paths = _extract_file_paths(sample)
    assert "db/transaction.py" in paths
    assert "tests/operators/test_ownership_validated_snapshot.py" in paths

    test_names = _extract_test_names(sample)
    assert "test_error_message_does_not_overclaim_enforcement" in test_names
    assert "test_ownership_validated_snapshot" not in test_names
