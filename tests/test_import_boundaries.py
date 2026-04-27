"""Verify import boundaries between layers (anti-cycle, anti-drift).

Rules (per spec §3.2):
- api/* may import: db/*, strategy/*, scanner/*, health, notifications
- api/* must NOT import: btc_api
- db/* must NOT import: api/*, scanner/*, btc_api (with one documented exception)
- scanner/* must NOT import: api/* routers (api/telegram is allowed as a service)
- strategy/* must NOT import anything outside strategy/

Documented exception: db/connection.py has a lazy `import btc_api` inside
_resolve_db_file() to honor the legacy `monkeypatch.setattr(btc_api, "DB_FILE", path)`
pattern in existing tests. The lazy import is inside a function body, not at
module level — the AST walk excludes it. Retained post-PR7 to avoid touching
50+ test fixtures across the suite.

Implementation: walk the AST of each module file and check imports
against an allowlist + denylist. Only top-level imports count (lazy imports
inside functions are intentional escape hatches).
"""
from __future__ import annotations

import ast
import pathlib

import pytest


PROJECT_ROOT = pathlib.Path(__file__).parent.parent

# (folder, denylist) pairs — top-level imports in <folder> must not match any
# prefix in <denylist>. Lazy imports inside function bodies are exempt.
DENYLIST_RULES = [
    ("api",     ["btc_api"]),
    ("db",      ["api", "scanner", "btc_api"]),
    ("scanner", [
        "api.ohlcv", "api.config", "api.positions", "api.signals",
        "api.kill_switch", "api.health", "api.tune", "api.notifications",
        "btc_api",
    ]),  # api.telegram is allowed (service, not router)
    ("strategy", ["api", "db", "scanner", "btc_api", "btc_scanner"]),
]


def _top_level_imports(path: pathlib.Path) -> list[str]:
    """Return module names from top-level imports only.

    Lazy imports inside function bodies are excluded — they are intentional
    escape hatches (e.g., db/connection.py:_resolve_db_file's `import btc_api`).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:  # only top-level statements, not nested
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


@pytest.mark.parametrize("folder,denylist", DENYLIST_RULES)
def test_import_boundaries(folder: str, denylist: list[str]) -> None:
    folder_path = PROJECT_ROOT / folder
    if not folder_path.exists():
        pytest.skip(f"{folder}/ does not exist yet")

    violations = []
    for py_file in folder_path.rglob("*.py"):
        # Skip dunder files like __init__.py and __main__.py
        if py_file.name.startswith("__"):
            continue
        imports = _top_level_imports(py_file)
        for imp in imports:
            for denied in denylist:
                if imp == denied or imp.startswith(denied + "."):
                    violations.append(
                        f"{py_file.relative_to(PROJECT_ROOT)} imports {imp!r} "
                        f"(denied: {denied})"
                    )

    assert not violations, "Import boundary violations:\n  " + "\n  ".join(violations)
