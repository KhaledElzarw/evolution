import json
from pathlib import Path

from tradebot.infrastructure.adapters.plugins.validator import (
    compute_code_hash,
    validate_bundle,
)

VALID_MANIFEST = {
    "schema_version": "manifest-v1",
    "strategy_id": "s1",
    "strategy_version_id": "sv1",
    "name": "Test",
    "family": "test",
    "origin": "builtin",
    "required_intervals": ["1m"],
    "min_warmup_candles": 5,
    "supported_symbol": "BTCUSDT",
    "code_hash": "x",
}

VALID_STRATEGY = '''
from decimal import Decimal
from tradebot.domain.strategies import StrategyDecision


class S:
    def initialize(self):
        return {}

    def on_market_snapshot(self, context, state):
        return StrategyDecision()


def create_strategy():
    return S()
'''


def make_bundle(tmp_path: Path, strategy_src: str = VALID_STRATEGY,
                manifest: dict | None = None) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(
        json.dumps(manifest or VALID_MANIFEST), encoding="utf-8")
    (bundle / "strategy.py").write_text(strategy_src, encoding="utf-8")
    return bundle


def test_valid_bundle_passes(tmp_path):
    report = validate_bundle(make_bundle(tmp_path))
    assert report.ok, report.errors
    assert report.code_hash == compute_code_hash(tmp_path / "bundle")


def test_missing_bundle_dir_fails(tmp_path):
    report = validate_bundle(tmp_path / "nope")
    assert not report.ok


def test_forbidden_imports_rejected(tmp_path):
    for bad in ("os", "sys", "subprocess", "socket", "requests", "httpx",
                "urllib", "pathlib", "shutil", "ctypes", "multiprocessing"):
        report = validate_bundle(
            make_bundle(tmp_path / bad, strategy_src=f"import {bad}\n"))
        assert not report.ok
        assert any("forbidden import" in e for e in report.errors), bad


def test_dynamic_execution_rejected(tmp_path):
    cases = {
        "eval": "x = eval('1+1')\n",
        "exec": "exec('pass')\n",
        "compile": "compile('1', '<s>', 'eval')\n",
        "dunder_import": "__import__('os')\n",
        "open": "open('f')\n",
        "alias_escape": "e = eval\n",
        "reflection": "x = ().__class__.__mro__[1].__subclasses__()\n",
    }
    for name, src in cases.items():
        report = validate_bundle(make_bundle(tmp_path / name, strategy_src=src))
        assert not report.ok, name


def test_reflection_escape_vectors_rejected(tmp_path):
    """Regression for the Phase-13 sandbox-escape finding: getattr + string
    dunders reached object.__subclasses__() (299 classes incl. os gadgets)
    despite passing the old validator. Every variant must now be rejected."""

    vectors = {
        "getattr_call": "x = getattr({}, 'update')\n",
        "setattr_call": "setattr(object(), 'a', 1)\n",
        "vars_call": "vars(object())\n",
        "dir_call": "dir(object())\n",
        "hasattr_call": "hasattr(object(), 'x')\n",
        "class_dunder": "c = ().__class__\n",
        "bases_dunder": "b = int.__bases__\n",
        "mro_dunder": "from decimal import Decimal\nm = Decimal.__mro__\n",
        "dict_dunder": "d = object().__dict__\n",
        "globals_dunder": "def f():\n    return f.__globals__\n",
        "dunder_string_literal": "s = '__class__'\n",
        # The exact escape chain proven to reach subprocess/os gadgets.
        "string_reflection_chain": (
            "def p():\n"
            "    base = getattr(getattr((), '__cl'), '__ba')\n"
            "    return base\n"
        ),
        "getattr_alias": "g = getattr\n",
    }
    for name, src in vectors.items():
        report = validate_bundle(make_bundle(tmp_path / name, strategy_src=src))
        assert not report.ok, f"escape vector not blocked: {name}"


def test_builtin_strategy_sources_have_no_reflection_constructs():
    """The hardening must not target legitimate strategy logic: no built-in
    uses getattr/setattr/vars/dir/hasattr or any dunder attribute/string.

    (Built-in *modules* use package-relative imports and __future__, which a
    distributable bundle would not, so this checks the reflection rules
    directly rather than running full bundle validation on package source.)"""

    import ast
    import inspect

    from tradebot.infrastructure.adapters.plugins.validator import (
        FORBIDDEN_CALL_NAMES,
        _DUNDER_RE,
    )
    from tradebot.strategies.builtin import BUILTIN_STRATEGIES

    offenders = []
    for cls in BUILTIN_STRATEGIES:
        tree = ast.parse(inspect.getsource(inspect.getmodule(cls)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and _DUNDER_RE.match(node.attr):
                offenders.append((cls.__name__, node.attr))
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in FORBIDDEN_CALL_NAMES):
                offenders.append((cls.__name__, node.func.id))
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                  and _DUNDER_RE.match(node.value)):
                offenders.append((cls.__name__, node.value))
    assert offenders == [], f"built-ins use forbidden constructs: {offenders}"


def test_relative_import_rejected(tmp_path):
    report = validate_bundle(
        make_bundle(tmp_path, strategy_src="from . import x\n"))
    assert not report.ok
    assert any("relative import" in e for e in report.errors)


def test_missing_required_files(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "strategy.py").write_text("x = 1\n", encoding="utf-8")
    report = validate_bundle(bundle)
    assert not report.ok
    assert any("manifest.json" in e for e in report.errors)


def test_manifest_schema_enforced(tmp_path):
    bad = dict(VALID_MANIFEST)
    del bad["code_hash"]
    bad["supported_symbol"] = "ETHUSDT"
    report = validate_bundle(make_bundle(tmp_path, manifest=bad))
    assert not report.ok
    assert any("code_hash" in e for e in report.errors)
    assert any("BTCUSDT" in e for e in report.errors)


def test_file_count_limit(tmp_path):
    bundle = make_bundle(tmp_path)
    for i in range(20):
        (bundle / f"extra_{i}.txt").write_text("x", encoding="utf-8")
    report = validate_bundle(bundle)
    assert not report.ok
    assert any("too many files" in e for e in report.errors)


def test_total_size_limit(tmp_path):
    bundle = make_bundle(tmp_path)
    (bundle / "big.txt").write_text("A" * (300 * 1024), encoding="utf-8")
    report = validate_bundle(bundle)
    assert not report.ok
    assert any("too large" in e for e in report.errors)


def test_syntax_error_rejected(tmp_path):
    report = validate_bundle(make_bundle(tmp_path, strategy_src="def broken(:\n"))
    assert not report.ok


def test_code_hash_changes_with_content(tmp_path):
    b1 = make_bundle(tmp_path / "a")
    b2 = make_bundle(tmp_path / "b", strategy_src=VALID_STRATEGY + "\n# v2\n")
    assert compute_code_hash(b1) != compute_code_hash(b2)
