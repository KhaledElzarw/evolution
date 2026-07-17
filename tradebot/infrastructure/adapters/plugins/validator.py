"""Static validation for untrusted strategy bundles (closes A05, part of A15/A17 class).

Defense-in-depth layer 1 of the plugin sandbox:

* Manifest schema validation.
* Bundle layout checks: no path traversal, no symlinks, no absolute paths,
  file-count and total-byte limits.
* AST import policy: explicit ALLOWED module list; everything else rejected.
* AST call policy: ``eval``/``exec``/``compile``/``__import__``/``open``/
  ``getattr``-escape and dunder reflection are rejected.

This is *not* claimed to be a complete malicious-code sandbox on its own; the
subprocess worker (worker.py) adds process isolation, and the operational docs
state the residual risk explicitly.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SDK_MANIFEST_VERSION = "manifest-v1"

# Modules generated strategy code MAY import. Everything else is rejected.
ALLOWED_IMPORTS: frozenset[str] = frozenset(
    {
        "math",
        "statistics",
        "decimal",
        "dataclasses",
        "enum",
        "typing",
        "collections",
        "itertools",
        "functools",
        "tradebot.domain.strategies",
        "tradebot.domain.ledger",
        "tradebot.domain.market",
        "tradebot.domain.money",
    }
)

FORBIDDEN_CALL_NAMES: frozenset[str] = frozenset(
    {"eval", "exec", "compile", "__import__", "open", "globals", "locals",
     "vars", "dir", "input", "breakpoint", "exit", "quit", "memoryview",
     # Reflection primitives: string-based attribute access is a sandbox-escape
     # vector (getattr(x, "__cl"+"ass__") defeats a static attribute blocklist),
     # so these are denied outright. Strategy code has no legitimate use for them.
     "getattr", "setattr", "delattr", "hasattr"}
)

# Any dunder attribute access is rejected — not just a hand-picked list. The
# classic escape chains through __class__ / __bases__ / __mro__ / __subclasses__
# / __globals__, and enumerating a blocklist is fragile. Strategies never need a
# dunder attribute, so deny the whole shape.
_DUNDER_RE = re.compile(r"^__.*__$")

MAX_FILES = 16
MAX_TOTAL_BYTES = 256 * 1024
REQUIRED_FILES = ("manifest.json", "strategy.py")

REQUIRED_MANIFEST_FIELDS = (
    "schema_version",
    "strategy_id",
    "strategy_version_id",
    "name",
    "family",
    "origin",
    "required_intervals",
    "min_warmup_candles",
    "supported_symbol",
    "code_hash",
)


@dataclass(slots=True)
class ValidationReport:
    ok: bool
    errors: list[str] = field(default_factory=list)
    code_hash: str | None = None

    def fail(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)


def _check_layout(bundle_dir: Path, report: ValidationReport) -> list[Path]:
    """Reject traversal/symlinks/size violations; return python files."""

    root = bundle_dir.resolve()
    files: list[Path] = []
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink():
            report.fail(f"symlink not allowed: {path.name}")
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            report.fail(f"path escapes bundle: {path}")
            continue
        files.append(path)
        total += path.stat().st_size
    if len(files) > MAX_FILES:
        report.fail(f"too many files: {len(files)} > {MAX_FILES}")
    if total > MAX_TOTAL_BYTES:
        report.fail(f"bundle too large: {total} > {MAX_TOTAL_BYTES}")
    names = {p.name for p in files if p.parent == root}
    for required in REQUIRED_FILES:
        if required not in names:
            report.fail(f"missing required file: {required}")
    return [p for p in files if p.suffix == ".py"]


def _check_manifest(bundle_dir: Path, report: ValidationReport) -> dict:
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        report.fail(f"manifest unreadable: {exc}")
        return {}
    if not isinstance(manifest, dict):
        report.fail("manifest must be a JSON object")
        return {}
    for fld in REQUIRED_MANIFEST_FIELDS:
        if fld not in manifest:
            report.fail(f"manifest missing field: {fld}")
    if manifest.get("schema_version") not in (SDK_MANIFEST_VERSION,):
        report.fail("unsupported manifest schema_version")
    if manifest.get("supported_symbol") != "BTCUSDT":
        report.fail("supported_symbol must be BTCUSDT")
    return manifest


def _check_python(py_path: Path, report: ValidationReport) -> None:
    source = py_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=py_path.name)
    except SyntaxError as exc:
        report.fail(f"{py_path.name}: syntax error: {exc}")
        return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    report.fail(f"{py_path.name}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level != 0:
                report.fail(f"{py_path.name}: relative import forbidden")
            elif module not in ALLOWED_IMPORTS:
                report.fail(f"{py_path.name}: forbidden import '{module}'")
        elif isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id in FORBIDDEN_CALL_NAMES:
                report.fail(f"{py_path.name}: forbidden call '{fn.id}'")
        elif isinstance(node, ast.Attribute):
            if _DUNDER_RE.match(node.attr):
                report.fail(f"{py_path.name}: forbidden dunder attribute "
                            f"'{node.attr}'")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_CALL_NAMES:
            # Referencing eval/exec/getattr without calling (aliasing escape).
            report.fail(f"{py_path.name}: forbidden name '{node.id}'")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A dunder passed as a string (e.g. to a reflection helper) has no
            # legitimate purpose and defeats attribute-name checks.
            if _DUNDER_RE.match(node.value):
                report.fail(f"{py_path.name}: forbidden dunder string "
                            f"'{node.value}'")


def compute_code_hash(bundle_dir: Path) -> str:
    """Deterministic hash of all bundle file contents (sorted by rel path)."""

    root = bundle_dir.resolve()
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_bundle(bundle_dir: Path) -> ValidationReport:
    """Full static validation. Never imports or executes the bundle code."""

    report = ValidationReport(ok=True)
    if not bundle_dir.is_dir():
        report.fail("bundle directory does not exist")
        return report
    py_files = _check_layout(bundle_dir, report)
    _check_manifest(bundle_dir, report)
    for py in py_files:
        _check_python(py, report)
    if report.ok:
        report.code_hash = compute_code_hash(bundle_dir)
    return report
