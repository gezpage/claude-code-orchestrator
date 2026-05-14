"""Detects no-op or broken scripts in a Node package.json."""

from __future__ import annotations

import json
import re

from orchestrator.verifiers.probes._types import ProbeContext, ProbeResult

PROBE_ID = "node_manifest_sanity"

# Heuristics for "this script doesn't actually run anything".
# Matches common no-op idioms: bare echo (no command following), `exit 0`,
# `true`, `:`, or a comment-only line. We intentionally allow `echo X && cmd`
# because those do real work.
_NOOP_PATTERNS = [
    re.compile(r"^\s*echo(\s+[^|&;]*)?\s*$"),
    re.compile(r"^\s*exit\s+0\s*$"),
    re.compile(r"^\s*true\s*$"),
    re.compile(r"^\s*:\s*$"),
    re.compile(r"^\s*#.*$"),
]


def _is_noop(script: str) -> bool:
    return any(pat.match(script) for pat in _NOOP_PATTERNS)


def run(ctx: ProbeContext) -> ProbeResult:
    findings: list[str] = []
    manifest = ctx.repo_root / "package.json"
    if not manifest.exists():
        return ProbeResult(id=PROBE_ID, status="passed")

    try:
        data = json.loads(manifest.read_text())
    except json.JSONDecodeError as exc:
        return ProbeResult(id=PROBE_ID, status="failed", findings=[f"package.json is not valid JSON: {exc}"])

    scripts = data.get("scripts") or {}
    if not isinstance(scripts, dict):
        return ProbeResult(id=PROBE_ID, status="failed", findings=["package.json 'scripts' is not an object"])

    for name, script in scripts.items():
        if not isinstance(script, str):
            findings.append(f"script '{name}' is not a string")
            continue
        if _is_noop(script):
            findings.append(f"script '{name}' appears to be a no-op: {script!r}")

    for required in ("name", "version"):
        if required not in data:
            findings.append(f"package.json missing required field '{required}'")

    status = "failed" if findings else "passed"
    return ProbeResult(id=PROBE_ID, status=status, findings=findings)
