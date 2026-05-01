"""Whitelist-based AST scanner — guard B for the locked holdout (epic A.1, #247).

Guard A (data/holdout_access.py) is opt-in ergonomics. This file is the
real net: it walks every .py file in the repo and fails if any
non-whitelisted module references the holdout in any of these patterns:

  1. String literal containing 'data/holdout' or naming 'holdout' as a path segment
  2. ``os.path.join(..., "holdout", ...)`` (and any other ``*.join(...)`` arg = "holdout")
  3. ``pathlib.Path(...) / "holdout" / ...``
  4. f-strings whose literal parts contain 'holdout' / 'data/holdout'

Docstrings are skipped to keep the manifest / provenance / module headers
quotable. Comments aren't visible to the AST so they're already skipped.

To use the holdout legitimately from a new module, either:
  (a) call ``data.holdout_access.open_holdout(rel_path, evaluation_mode=True)``
      and never reference ``"data/holdout"`` directly, or
  (b) add the module path to ``HOLDOUT_LEGITIMATE_MODULES`` below with a
      one-line justification — the addition is reviewed in the PR.

Known limitations
-----------------
This scanner is **defense against a distracted human, not a motivated attacker**.
Patterns it does NOT catch by construction:

  * String concatenation of literals: ``"data/" + "hold" + "out/foo"``.
    (BinOp(Add) inspection could be added; not done here because it inflates
    the false-positive surface for concatenation in unrelated code.)
  * Variable indirection: ``x = "ho"; y = x + "ldout"; open("data/" + y)``.
  * Encoded / obfuscated paths: ``bytes.fromhex(...).decode()``,
    ``base64.b64decode(...)``, ``codecs.decode(...)``.
  * Dynamic resolution: ``getattr(mod, "holdout")``, ``importlib`` games.
  * Reading via subprocess (``subprocess.run(["cat", "data/holdout/..."])``)
    — string is in a list, currently not inspected.

If a contributor goes to those lengths to bypass the guard, the PR review is
the next layer; the code-review checklist for Epic-A-related changes should
include "did you read data/holdout/ from a non-whitelisted module".
"""
from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Modules permitted to reference the holdout. Adding a path here is a
# review-gated decision — the PR reviewer is the structural backstop.
HOLDOUT_LEGITIMATE_MODULES: set[str] = {
    # the access wrapper itself
    "data/holdout_access.py",
    # the one-shot lock script — writes the snapshot, must reference the path
    "scripts/lock_holdout.py",
    # this scanner — contains the patterns it looks for
    "tests/test_holdout_isolation.py",
    # A.4-1 pre-holdout retune wrapper (#250). Reads data/ohlcv.db only
    # (NOT data/holdout/) but names its artefact directory with the
    # '-pre-holdout' suffix for human discoverability. The output path
    # carries the literal token; no holdout data is consumed.
    "tools/retune_pre_holdout.py",
    # A.2 walk-forward harness modules and A.4 evaluation modules will be
    # added here when those tickets land.
}

# Directories that don't contain trading code; scanning them would be noise
# and could surface third-party strings that legitimately mention 'holdout'.
EXCLUDED_DIR_PARTS: set[str] = {
    ".git", ".worktrees", ".venv", "venv", "env",
    "__pycache__", "node_modules", "frontend",
    "data/holdout",          # the locked dataset itself (no .py inside, defensive)
    "data/backtest",         # CSV cache (no .py)
    "data/_archive",         # future home of dead-symbol CSVs (#273)
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

HOLDOUT_TOKEN = "holdout"
HOLDOUT_PATH_TOKEN = "data/holdout"


def _module_rel(path: pathlib.Path) -> str:
    return str(path.relative_to(REPO_ROOT)).replace("\\", "/")


def _is_excluded(path: pathlib.Path) -> bool:
    rel = _module_rel(path)
    for excluded in EXCLUDED_DIR_PARTS:
        if rel.startswith(excluded + "/") or rel == excluded:
            return True
    return False


def _is_holdout_path_segment(value: str) -> bool:
    """True if `value` reads as a 'holdout' path segment, not just any substring.

    This avoids flagging benign strings that happen to contain the substring
    'holdout' inside an unrelated word (the only realistic case being someone
    naming a function 'placeholdout' — defensive, but cheap).
    """
    if value == HOLDOUT_TOKEN:
        return True
    if HOLDOUT_PATH_TOKEN in value:
        return True
    if f"/{HOLDOUT_TOKEN}/" in value:
        return True
    if value.endswith(f"/{HOLDOUT_TOKEN}"):
        return True
    if value.startswith(f"{HOLDOUT_TOKEN}/"):
        return True
    return False


class HoldoutReferenceVisitor(ast.NodeVisitor):
    """AST visitor that records holdout references with line numbers and reason."""

    def __init__(self, rel_path: str):
        self.rel_path = rel_path
        self.violations: list[tuple[int, str]] = []
        self._docstring_lines: set[int] = set()

    # ── docstring skip ─────────────────────────────────────────────────────
    def _collect_docstring_lines(self, module: ast.Module) -> None:
        for node in ast.walk(module):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if not node.body:
                    continue
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                        and isinstance(first.value.value, str):
                    start = first.lineno
                    end = first.end_lineno or start
                    for line in range(start, end + 1):
                        self._docstring_lines.add(line)

    def visit_Module(self, node: ast.Module) -> None:
        self._collect_docstring_lines(node)
        self.generic_visit(node)

    def _record(self, node: ast.AST, reason: str) -> None:
        line = getattr(node, "lineno", 0)
        if line in self._docstring_lines:
            return
        self.violations.append((line, reason))

    # ── pattern 1: string literal ──────────────────────────────────────────
    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and _is_holdout_path_segment(node.value):
            self._record(node, f"string literal: {node.value!r}")

    # ── pattern 4: f-strings (literal parts) ───────────────────────────────
    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                if _is_holdout_path_segment(part.value) or HOLDOUT_TOKEN in part.value:
                    self._record(node, f"f-string literal part: {part.value!r}")
        self.generic_visit(node)

    # ── pattern 2: *.join(..., "holdout", ...) ─────────────────────────────
    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute) and node.func.attr == "join":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value == HOLDOUT_TOKEN or _is_holdout_path_segment(arg.value):
                        self._record(node, f"*.join() arg is 'holdout': {arg.value!r}")
        self.generic_visit(node)

    # ── pattern 3: Path(...) / "holdout" / ... ─────────────────────────────
    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Div):
            for operand in (node.left, node.right):
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                    if operand.value == HOLDOUT_TOKEN or _is_holdout_path_segment(operand.value):
                        self._record(node, f"Path / 'holdout' construction: {operand.value!r}")
        self.generic_visit(node)


def _scan(path: pathlib.Path) -> list[tuple[int, str]]:
    rel = _module_rel(path)
    if rel in HOLDOUT_LEGITIMATE_MODULES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        # Files that don't parse as Python (rare; e.g. partial / templated) — skip.
        return []
    visitor = HoldoutReferenceVisitor(rel)
    visitor.visit(tree)
    return visitor.violations


def _all_python_files() -> list[pathlib.Path]:
    return sorted(p for p in REPO_ROOT.rglob("*.py") if not _is_excluded(p))


# ─────────────────────────────────────────────────────────────────────────────
# Repo-wide assertion — the actual contamination guard
# ─────────────────────────────────────────────────────────────────────────────


def test_no_holdout_references_in_non_whitelisted_modules():
    """Scan all repo Python files; any holdout reference outside the whitelist fails CI."""
    findings: dict[str, list[tuple[int, str]]] = {}
    for path in _all_python_files():
        violations = _scan(path)
        if violations:
            findings[_module_rel(path)] = violations

    if findings:
        lines = ["Holdout references detected in non-whitelisted modules:"]
        for module, viols in findings.items():
            for line_no, reason in viols:
                lines.append(f"  {module}:{line_no}  {reason}")
        lines.append("")
        lines.append("Either:")
        lines.append("  (a) use data.holdout_access.open_holdout(rel_path, evaluation_mode=True), or")
        lines.append("  (b) add the module to HOLDOUT_LEGITIMATE_MODULES with a justification.")
        pytest.fail("\n".join(lines))


# ─────────────────────────────────────────────────────────────────────────────
# Demonstrative tests — prove each reinforcement pattern is caught
# ─────────────────────────────────────────────────────────────────────────────


def _scan_inline(code: str) -> list[tuple[int, str]]:
    visitor = HoldoutReferenceVisitor("inline_test.py")
    visitor.visit(ast.parse(code))
    return visitor.violations


def test_pattern_1_string_literal_is_caught():
    code = 'import pandas as pd\ndf = pd.read_csv("data/holdout/foo.csv")\n'
    violations = _scan_inline(code)
    assert violations, "should detect 'data/holdout/foo.csv' string literal"
    assert any("string literal" in r for _, r in violations)


def test_known_limitation_concatenation_is_not_caught():
    """Documents that the scanner does NOT catch string concatenation by design.

    See module docstring 'Known limitations'. This is not a defect — concatenation
    can be added if needed, but inflates the false-positive surface elsewhere in
    the repo. The PR-review checklist for Epic-A-related changes is the
    backstop for this evasion class.
    """
    code = 'import pandas as pd\ndf = pd.read_csv("data/" + "hold" + "out/foo.csv")\n'
    violations = _scan_inline(code)
    assert not violations, (
        "concatenation is intentionally not caught; if this fires, the visitor "
        "was extended — update the Known limitations section in the module docstring."
    )


def test_pattern_2_os_path_join_is_caught():
    code = (
        "import os\n"
        'DATA_DIR = "data"\n'
        'path = os.path.join(DATA_DIR, "holdout", "ohlcv.sqlite")\n'
    )
    violations = _scan_inline(code)
    assert violations, "should detect os.path.join(..., 'holdout', ...)"
    assert any("join()" in r for _, r in violations)


def test_pattern_3_pathlib_division_is_caught():
    code = (
        "from pathlib import Path\n"
        'path = Path("data") / "holdout" / "ohlcv.sqlite"\n'
    )
    violations = _scan_inline(code)
    assert violations, "should detect Path / 'holdout' / ..."
    assert any("Path / 'holdout'" in r for _, r in violations)


def test_pattern_4_fstring_is_caught():
    code = (
        'name = "ohlcv"\n'
        'path = f"data/holdout/{name}.sqlite"\n'
    )
    violations = _scan_inline(code)
    assert violations, "should detect f-string literal part with 'holdout'"
    assert any("f-string" in r for _, r in violations)


def test_pattern_4_fstring_with_holdout_segment():
    code = (
        'base = "/tmp"\n'
        'path = f"{base}/holdout/foo.txt"\n'
    )
    violations = _scan_inline(code)
    assert violations, "should detect f-string with 'holdout' as path segment"


def test_docstrings_are_skipped():
    code = (
        '"""This module references data/holdout in a docstring — should NOT fail."""\n'
        "x = 1\n"
    )
    violations = _scan_inline(code)
    assert not violations, f"docstring should be skipped, got: {violations}"


def test_function_docstring_is_skipped():
    code = (
        "def evaluate():\n"
        '    """Docstring mentions data/holdout — must not fail."""\n'
        "    return 1\n"
    )
    violations = _scan_inline(code)
    assert not violations, f"function docstring should be skipped, got: {violations}"


def test_unrelated_string_does_not_fire():
    code = 'x = "data/backtest/foo.csv"\ny = "placeholder"\n'
    violations = _scan_inline(code)
    assert not violations, f"unrelated paths must not fire, got: {violations}"


# ─────────────────────────────────────────────────────────────────────────────
# Whitelist sanity — wrapper module exists and is the legitimate entry point
# ─────────────────────────────────────────────────────────────────────────────


def test_wrapper_module_exists_and_exposes_open_holdout():
    wrapper_rel = "data/holdout_access.py"
    assert wrapper_rel in HOLDOUT_LEGITIMATE_MODULES
    wrapper_path = REPO_ROOT / wrapper_rel
    assert wrapper_path.exists(), f"wrapper missing at {wrapper_path}"
    text = wrapper_path.read_text()
    assert "def open_holdout(" in text
    assert "class HoldoutAccessError" in text
    assert "evaluation_mode" in text


def test_wrapper_raises_without_evaluation_mode():
    """The wrapper itself must refuse access if evaluation_mode is not literally True."""
    from data.holdout_access import HoldoutAccessError, open_holdout

    with pytest.raises(HoldoutAccessError):
        open_holdout("MANIFEST.json", evaluation_mode=False)
    with pytest.raises(HoldoutAccessError):
        # truthy non-bool is rejected — `is not True`, not `not bool(...)`
        open_holdout("MANIFEST.json", evaluation_mode=1)  # type: ignore[arg-type]


def test_wrapper_path_traversal_is_refused():
    from data.holdout_access import HoldoutAccessError, open_holdout

    with pytest.raises(HoldoutAccessError):
        open_holdout("../ohlcv.db", evaluation_mode=True)
