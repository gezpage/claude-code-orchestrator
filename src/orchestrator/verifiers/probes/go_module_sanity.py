"""Sanity checks for a Go module: well-formed go.mod, go.sum present when deps exist."""

from __future__ import annotations

import re

from orchestrator.verifiers.probes._types import ProbeContext, ProbeResult

PROBE_ID = "go_module_sanity"

_MODULE_RE = re.compile(r"^\s*module\s+\S+\s*$", re.MULTILINE)
_REQUIRE_BLOCK_RE = re.compile(r"^\s*require\s*\(", re.MULTILINE)
_REQUIRE_LINE_RE = re.compile(r"^\s*require\s+\S+", re.MULTILINE)


def run(ctx: ProbeContext) -> ProbeResult:
    findings: list[str] = []
    go_mod = ctx.repo_root / "go.mod"
    if not go_mod.exists():
        return ProbeResult(id=PROBE_ID, status="passed")

    text = go_mod.read_text()
    if not _MODULE_RE.search(text):
        findings.append("go.mod is missing a 'module' declaration")

    has_deps = bool(_REQUIRE_BLOCK_RE.search(text) or _REQUIRE_LINE_RE.search(text))
    if has_deps and not (ctx.repo_root / "go.sum").exists():
        findings.append("go.mod declares dependencies but go.sum is missing — run 'go mod tidy'")

    status = "failed" if findings else "passed"
    return ProbeResult(id=PROBE_ID, status=status, findings=findings)
