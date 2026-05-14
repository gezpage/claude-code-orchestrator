"""Unit tests for the deterministic package manifest checker (ADR-017).

These tests focus on the checks themselves: fake quality scripts, missing
script targets, and the likely-unused-dependency heuristic. The orchestrator
integration (pre-pass before QA, blocking gate) is covered separately."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator import manifest_check


def _write_manifest(repo: Path, payload: dict) -> None:
    (repo / "package.json").write_text(json.dumps(payload))


def _src(repo: Path, relpath: str, content: str = "") -> Path:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ── presence & shape ──────────────────────────────────────────────────────────


def test_no_package_json_returns_none(tmp_path):
    assert manifest_check.check_manifest(tmp_path) is None


def test_empty_manifest_no_findings(tmp_path):
    _write_manifest(tmp_path, {})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert report.findings == []
    assert not report.has_blocking


def test_invalid_json_is_blocking(tmp_path):
    (tmp_path / "package.json").write_text("{ not json")
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert report.has_blocking
    assert any("not valid JSON" in f.message for f in report.findings)


# ── fake quality scripts ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "echo add eslint",
        "echo TODO",
        "true",
        "exit 0",
        ":",
        "",
        "   ",
    ],
)
def test_fake_lint_script_is_blocking(tmp_path, cmd):
    _write_manifest(tmp_path, {"scripts": {"lint": cmd}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None and report.has_blocking
    findings = [f for f in report.findings if f.type == "fake_script"]
    assert findings, f"expected fake_script finding for cmd={cmd!r}"


def test_real_lint_script_not_flagged(tmp_path):
    _src(tmp_path, "src/index.ts", "")  # so we have something to lint
    _write_manifest(tmp_path, {"scripts": {"lint": "eslint src"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "fake_script" for f in report.findings)


def test_fake_test_and_typecheck_blocked(tmp_path):
    _write_manifest(
        tmp_path,
        {"scripts": {"test": "echo no tests", "typecheck": "exit 0", "format": "echo run prettier"}},
    )
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    blocked = {f.message for f in report.findings if f.blocking}
    assert any("test" in m for m in blocked)
    assert any("typecheck" in m for m in blocked)
    assert any("format" in m for m in blocked)


def test_namespaced_quality_scripts_checked(tmp_path):
    _write_manifest(tmp_path, {"scripts": {"lint:fix": "echo nope"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None and report.has_blocking


def test_non_quality_script_with_echo_not_flagged(tmp_path):
    _write_manifest(tmp_path, {"scripts": {"hello": "echo hi"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "fake_script" for f in report.findings)


# ── missing script targets ────────────────────────────────────────────────────


def test_missing_node_target_is_blocking(tmp_path):
    _write_manifest(tmp_path, {"scripts": {"start": "node src/server.js"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None and report.has_blocking
    assert any(f.type == "missing_script_target" for f in report.findings)


def test_present_node_target_not_flagged(tmp_path):
    _src(tmp_path, "src/server.js", "// server")
    _write_manifest(tmp_path, {"scripts": {"start": "node src/server.js"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "missing_script_target" for f in report.findings)


def test_python_target_resolves(tmp_path):
    _src(tmp_path, "scripts/seed.py", "")
    _write_manifest(tmp_path, {"scripts": {"seed": "python scripts/seed.py"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "missing_script_target" for f in report.findings)


def test_shell_pipeline_script_not_resolved(tmp_path):
    """Composite commands are intentionally not resolved — too many false positives."""
    _write_manifest(tmp_path, {"scripts": {"build": "node missing.js && echo ok"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "missing_script_target" for f in report.findings)


def test_npm_run_chain_not_resolved(tmp_path):
    """`npm run` delegation isn't resolved as a direct target."""
    _write_manifest(tmp_path, {"scripts": {"build": "npm run lint && npm run compile"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    # No node/python prefix → no resolution attempt → no false positive.
    assert not any(f.type == "missing_script_target" for f in report.findings)


def test_absolute_path_target_not_resolved(tmp_path):
    _write_manifest(tmp_path, {"scripts": {"start": "node /usr/local/bin/foo.js"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "missing_script_target" for f in report.findings)


# ── unused-dependency heuristic ───────────────────────────────────────────────


def test_dep_used_via_require_not_flagged(tmp_path):
    _src(tmp_path, "src/index.js", "const x = require('lodash');\n")
    _write_manifest(tmp_path, {"dependencies": {"lodash": "^4.0.0"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "unused_dependency" for f in report.findings)


def test_dep_used_via_import_not_flagged(tmp_path):
    _src(tmp_path, "src/index.ts", "import express from 'express';\n")
    _write_manifest(tmp_path, {"dependencies": {"express": "^5.0.0"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "unused_dependency" for f in report.findings)


def test_dep_used_via_subpath_import_not_flagged(tmp_path):
    _src(tmp_path, "src/a.ts", "import { z } from 'zod/lib';\n")
    _write_manifest(tmp_path, {"dependencies": {"zod": "^3.0.0"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert not any(f.type == "unused_dependency" for f in report.findings)


def test_unused_dep_is_advisory_not_blocking(tmp_path):
    _src(tmp_path, "src/index.ts", "console.log('hi')\n")
    _write_manifest(tmp_path, {"dependencies": {"express": "^5.0.0"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    advisory = [f for f in report.findings if f.type == "unused_dependency"]
    assert advisory, "expected unused_dependency advisory"
    assert all(not f.blocking for f in advisory)
    assert not report.has_blocking


def test_node_modules_not_scanned_for_dep_usage(tmp_path):
    """A reference inside node_modules must not count as usage."""
    _src(tmp_path, "node_modules/lodash/index.js", "require('lodash')")
    _src(tmp_path, "src/index.ts", "console.log('hi')\n")
    _write_manifest(tmp_path, {"dependencies": {"lodash": "^4.0.0"}})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    assert any(f.type == "unused_dependency" and "lodash" in f.message for f in report.findings)


# ── write_report ──────────────────────────────────────────────────────────────


def test_write_report_emits_json_and_markdown(tmp_path):
    _src(tmp_path, "src/index.ts", "import x from 'lodash';\n")
    _write_manifest(
        tmp_path,
        {
            "scripts": {"lint": "echo add eslint"},
            "dependencies": {"lodash": "^4.0.0"},
        },
    )
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    out = tmp_path / "verify"
    json_path, md_path = manifest_check.write_report(report, out)

    payload = json.loads(json_path.read_text())
    assert payload["has_blocking"] is True
    assert any(f["type"] == "fake_script" for f in payload["findings"])
    md = md_path.read_text()
    assert "Blocking" in md
    assert "fake_script" in md


def test_write_report_no_findings_renders_clean_markdown(tmp_path):
    _write_manifest(tmp_path, {})
    report = manifest_check.check_manifest(tmp_path)
    assert report is not None
    out = tmp_path / "verify"
    _json_path, md_path = manifest_check.write_report(report, out)
    assert "No findings" in md_path.read_text()
