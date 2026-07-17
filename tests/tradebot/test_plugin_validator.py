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
