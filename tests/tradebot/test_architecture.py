"""Phase 2 acceptance: enforce the layered dependency direction.

Domain must not import FastAPI, HTTP clients, SQLAlchemy, subprocess,
filesystem/env access, or the local model. Application may depend on domain
but not on infrastructure/api.
"""

from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOMAIN = ROOT / "tradebot" / "domain"

FORBIDDEN_IN_DOMAIN = {
    "fastapi", "httpx", "requests", "sqlalchemy", "subprocess", "socket",
    "os", "sys", "pathlib", "dotenv",
}


def _imports(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_domain_has_no_infrastructure_imports():
    offenders = {}
    for py in DOMAIN.glob("*.py"):
        bad = _imports(py) & FORBIDDEN_IN_DOMAIN
        if bad:
            offenders[py.name] = bad
    assert not offenders, f"domain layer leaks: {offenders}"


def test_domain_does_not_import_application_or_infrastructure():
    for py in DOMAIN.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "tradebot.application" not in text
        assert "tradebot.infrastructure" not in text
        assert "tradebot.api" not in text
