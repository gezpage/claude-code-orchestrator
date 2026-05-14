# Deterministic package manifest checker. Runs before QA for Node repositories to
# catch fake/no-op quality scripts, missing script targets, and likely-unused
# production dependencies — failure modes that LLM reviewers consistently miss
# or downgrade. See ADR-017.
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Script names recognised as "quality gates" — fake placeholders in these are blocking.
_QUALITY_SCRIPT_NAMES = {"lint", "typecheck", "type-check", "typecheck:ci", "test", "format", "check"}

# Patterns that mark a script as a no-op placeholder rather than a real command.
_FAKE_SCRIPT_RE = re.compile(
    r"""^\s*(
        echo\b           # `echo add eslint`, `echo TODO`
        | true\b         # `true`
        | exit\s+0\b     # `exit 0`
        | :\s*$          # bare `:`
    )""",
    re.VERBOSE,
)

# Commands of the form `node <file>` or `python <file>` whose first positional argument
# we can resolve against the repo. We only resolve unambiguous file paths — anything
# more complex (subshells, env-prefix, npm-run chains) is left alone deliberately.
_DIRECT_RUNNER_BINS = {"node", "python", "python3"}

_FINDING_TYPE = Literal["fake_script", "missing_script_target", "unused_dependency"]


@dataclass(frozen=True)
class Finding:
    """A single manifest finding. `blocking=True` ones gate the pipeline."""

    type: _FINDING_TYPE
    blocking: bool
    message: str
    detail: str = ""


@dataclass
class ManifestReport:
    repo_root: str
    manifest_path: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def has_blocking(self) -> bool:
        return any(f.blocking for f in self.findings)

    def blocking_summary(self) -> str:
        bf = [f for f in self.findings if f.blocking]
        return "; ".join(f.message for f in bf)


def check_manifest(repo_root: str | Path) -> ManifestReport | None:
    """Run the deterministic manifest checks against ``repo_root``.

    Returns ``None`` if there is no ``package.json`` — non-Node repos are a no-op
    rather than an error. Returns a ``ManifestReport`` (possibly with no findings)
    otherwise. Malformed JSON is itself recorded as a blocking finding."""
    repo = Path(repo_root)
    manifest = repo / "package.json"
    if not manifest.is_file():
        return None

    report = ManifestReport(repo_root=str(repo), manifest_path=str(manifest))

    try:
        raw = manifest.read_text()
    except OSError as exc:
        report.findings.append(
            Finding(
                type="missing_script_target",
                blocking=True,
                message="package.json could not be read",
                detail=str(exc),
            )
        )
        return report

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        report.findings.append(
            Finding(
                type="missing_script_target",
                blocking=True,
                message="package.json is not valid JSON",
                detail=str(exc),
            )
        )
        return report

    scripts = data.get("scripts") or {}
    if isinstance(scripts, dict):
        _check_fake_scripts(scripts, report)
        _check_script_targets(scripts, repo, report)

    deps = data.get("dependencies") or {}
    if isinstance(deps, dict) and deps:
        _check_unused_dependencies(deps, repo, report)

    return report


def write_report(report: ManifestReport, out_dir: Path) -> tuple[Path, Path]:
    """Persist the report as both JSON (for tooling) and Markdown (for humans/LLMs).

    Returns the two written paths. JSON is the contract; Markdown is the
    reviewer-facing rendering."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "manifest-findings.json"
    md_path = out_dir / "manifest-findings.md"

    payload = {
        "repo_root": report.repo_root,
        "manifest_path": report.manifest_path,
        "has_blocking": report.has_blocking,
        "findings": [
            {
                "type": f.type,
                "blocking": f.blocking,
                "message": f.message,
                "detail": f.detail,
            }
            for f in report.findings
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n")

    md_path.write_text(_render_markdown(report))
    return json_path, md_path


def _check_fake_scripts(scripts: dict, report: ManifestReport) -> None:
    for name, cmd in scripts.items():
        if not isinstance(cmd, str):
            continue
        bare = name.split(":", 1)[0]
        if bare not in _QUALITY_SCRIPT_NAMES:
            continue
        stripped = cmd.strip()
        if not stripped:
            report.findings.append(
                Finding(
                    type="fake_script",
                    blocking=True,
                    message=f'script "{name}" is empty',
                    detail=cmd,
                )
            )
            continue
        if _FAKE_SCRIPT_RE.match(stripped):
            report.findings.append(
                Finding(
                    type="fake_script",
                    blocking=True,
                    message=f'script "{name}" is a no-op placeholder',
                    detail=cmd,
                )
            )


def _check_script_targets(scripts: dict, repo: Path, report: ManifestReport) -> None:
    for name, cmd in scripts.items():
        if not isinstance(cmd, str):
            continue
        target = _extract_direct_runner_target(cmd)
        if target is None:
            continue
        resolved = (repo / target).resolve()
        try:
            resolved.relative_to(repo.resolve())
        except ValueError:
            # Path escapes the repo — don't try to validate it.
            continue
        if not resolved.exists():
            report.findings.append(
                Finding(
                    type="missing_script_target",
                    blocking=True,
                    message=f'script "{name}" points to a missing file: {target}',
                    detail=cmd,
                )
            )


def _extract_direct_runner_target(cmd: str) -> str | None:
    """Pull the first positional argument from a `node <file>` / `python <file>` command.

    Returns ``None`` for shell pipelines, subshells, chains (`&&`, `||`, `;`, `|`),
    npm-run delegation, or anything with shell metacharacters past the binary —
    we deliberately only resolve unambiguous direct invocations."""
    if any(meta in cmd for meta in ("&&", "||", ";", "|", "`", "$(")):
        return None
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None
    if not tokens:
        return None
    binary = Path(tokens[0]).name
    if binary not in _DIRECT_RUNNER_BINS:
        return None
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        if tok.startswith("/"):
            return None  # absolute path — leave alone
        # Treat the first non-flag positional as the target.
        return tok
    return None


_IMPORT_PATTERNS = (
    'require("{name}")',
    "require('{name}')",
    'from "{name}"',
    "from '{name}'",
    'import "{name}"',
    "import '{name}'",
    'from "{name}/',
    "from '{name}/",
    'require("{name}/',
    "require('{name}/",
)

_SOURCE_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts"}
_SKIP_DIRS = {"node_modules", "dist", "build", "coverage", ".git", ".next", ".turbo", "out"}


def _check_unused_dependencies(deps: dict, repo: Path, report: ManifestReport) -> None:
    """Flag production deps that don't appear in any source-file import.

    Heuristic and intentionally conservative — package.json itself is excluded
    (the dep name appears there by definition), as are common build output dirs.
    Hits at most a few hundred files; if scanning fails for any reason we skip
    silently rather than spam the report."""
    sources = list(_iter_source_files(repo))
    if not sources:
        return
    blob = "\n".join(_read_text_safe(p) for p in sources)
    for dep_name in deps:
        if not isinstance(dep_name, str) or not dep_name:
            continue
        if not _dep_referenced(dep_name, blob):
            report.findings.append(
                Finding(
                    type="unused_dependency",
                    blocking=False,
                    message=f'production dependency "{dep_name}" appears unused',
                    detail="No require/import reference found in repo source files (heuristic — verify before removing).",
                )
            )


def _dep_referenced(dep: str, blob: str) -> bool:
    return any(pat.format(name=dep) in blob for pat in _IMPORT_PATTERNS)


def _iter_source_files(repo: Path):
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in _SOURCE_EXTS:
            continue
        if any(part in _SKIP_DIRS or part.startswith(".") for part in path.relative_to(repo).parts[:-1]):
            continue
        yield path


def _read_text_safe(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _render_markdown(report: ManifestReport) -> str:
    if not report.findings:
        return f"# Manifest Findings\n\nManifest: `{report.manifest_path}`\n\nNo findings.\n"

    lines = [
        "# Manifest Findings",
        "",
        f"Manifest: `{report.manifest_path}`",
        "",
    ]
    blocking = [f for f in report.findings if f.blocking]
    advisory = [f for f in report.findings if not f.blocking]

    if blocking:
        lines.append("## Blocking")
        lines.append("")
        for f in blocking:
            lines.append(f"- **{f.type}** — {f.message}")
            if f.detail:
                lines.append(f"  - `{f.detail}`")
        lines.append("")

    if advisory:
        lines.append("## Advisory")
        lines.append("")
        for f in advisory:
            lines.append(f"- **{f.type}** — {f.message}")
            if f.detail:
                lines.append(f"  - {f.detail}")
        lines.append("")

    return "\n".join(lines)
