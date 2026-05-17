"""Deterministic project bootstrap for new target repos.

Creates `.cco.yaml` (and minimal marker files when absent) so deterministic
verification can detect a toolchain on a fresh repo. Templates are static,
file content is checked-in YAML/JSON/TOML — no LLM is invoked. See ADR-037.

Public surface:
- ``SUPPORTED_TOOLCHAINS``: ordered list of toolchain identifiers.
- ``STANDARDS_FOR_TOOLCHAIN``: mapping to harsh-*-engineering-standards id.
- ``BootstrapPlan`` / ``FileChange``: structured description of planned writes.
- ``plan_bootstrap``: build a plan without touching disk.
- ``apply_plan``: write the planned changes, return list of paths written.
- ``commit_changes``: stage + commit the bootstrap with a fixed message.
- ``looks_unbootstrapped``: best-effort detection used at pipeline start.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from orchestrator.verifiers.detection import detect_toolchain
from orchestrator.verifiers.recipe import load_bundled_recipes

SUPPORTED_TOOLCHAINS: tuple[str, ...] = (
    "python",
    "node",
    "typescript",
    "php",
    "go",
    "java",
)

# Maps a bootstrap toolchain id to the harsh-*-engineering-standards identifier
# that orchestrator.standards.discover() returns. "node" maps to "nodejs" because
# the skill folder is `harsh-nodejs-engineering-standards`. "php" has no skill
# today, so it is intentionally absent from this map.
STANDARDS_FOR_TOOLCHAIN: dict[str, str] = {
    "python": "python",
    "node": "nodejs",
    "typescript": "typescript",
    "go": "go",
    "java": "java",
}


@dataclass(frozen=True)
class FileChange:
    """One planned filesystem write."""

    path: Path
    contents: str
    # True iff a file already exists at `path` with different contents.
    # The applier refuses to overwrite unless force=True.
    conflicts: bool
    # True iff a file already exists at `path` with identical contents.
    # The applier treats these as no-ops.
    already_present: bool


@dataclass
class BootstrapPlan:
    toolchain: str
    repo_root: Path
    files: list[FileChange] = field(default_factory=list)

    @property
    def new_files(self) -> list[FileChange]:
        return [f for f in self.files if not f.conflicts and not f.already_present]

    @property
    def conflicts(self) -> list[FileChange]:
        return [f for f in self.files if f.conflicts]

    @property
    def already_present(self) -> list[FileChange]:
        return [f for f in self.files if f.already_present]


# ── Templates ─────────────────────────────────────────────────────────────────
#
# Each template is keyed by toolchain. The first entry must be `.cco.yaml`; any
# additional entries are minimal marker/config files that the recipe needs to
# select a toolchain when the repo is otherwise empty. Files that already exist
# in the target repo are left untouched unless their content differs — at which
# point the applier refuses without `force=True`.

_CCO_YAML_PYTHON = """\
verification:
  toolchain: python
  commands:
    - id: test
      command: python -m pytest
      required: true
      timeout_seconds: 600
"""

_CCO_YAML_NODE = """\
verification:
  toolchain: node
  commands:
    - id: test
      command: npm test
      required: true
      if_script_exists: test
      timeout_seconds: 600
    - id: lint
      command: npm run lint
      required: false
      if_script_exists: lint
    - id: typecheck
      command: npm run typecheck
      required: false
      if_script_exists: typecheck
    - id: build
      command: npm run build
      required: false
      if_script_exists: build
"""

_CCO_YAML_TYPESCRIPT = """\
verification:
  toolchain: typescript
  commands:
    - id: test
      command: npm test
      required: true
      if_script_exists: test
      timeout_seconds: 600
    - id: typecheck
      command: npm run typecheck
      required: false
      if_script_exists: typecheck
    - id: lint
      command: npm run lint
      required: false
      if_script_exists: lint
    - id: build
      command: npm run build
      required: false
      if_script_exists: build
"""

_CCO_YAML_PHP = """\
verification:
  toolchain: php
  commands:
    - id: composer-test
      command: composer test
      required: false
      if_composer_script_exists: test
      timeout_seconds: 600
    - id: phpunit
      command: vendor/bin/phpunit
      required: false
      if_file_exists: vendor/bin/phpunit
      timeout_seconds: 600
"""

_CCO_YAML_GO = """\
verification:
  toolchain: go
  commands:
    - id: build
      command: go build ./...
      required: true
      timeout_seconds: 600
    - id: test
      command: go test ./...
      required: true
      timeout_seconds: 600
    - id: vet
      command: go vet ./...
      required: false
"""

_CCO_YAML_JAVA = """\
verification:
  toolchain: java
  commands:
    - id: test_mvnw
      command: ./mvnw test
      required: true
      if_file_exists: mvnw
      timeout_seconds: 900
    - id: test_maven
      command: mvn test
      required: true
      if_file_exists: pom.xml
      if_file_not_exists: mvnw
      timeout_seconds: 900
    - id: test_gradlew
      command: ./gradlew test
      required: true
      if_file_exists: gradlew
      timeout_seconds: 900
    - id: test_gradle
      command: gradle test
      required: true
      if_file_exists: "build.gradle*"
      if_file_not_exists: gradlew
      timeout_seconds: 900
"""

_PYPROJECT_TOML = """\
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "app"
version = "0.0.1"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
"""

_PACKAGE_JSON = """\
{
  "name": "app",
  "version": "0.0.1",
  "private": true,
  "scripts": {
    "test": "echo \\"no tests configured\\" && exit 1"
  }
}
"""

_TSCONFIG_JSON = """\
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src"]
}
"""

_COMPOSER_JSON = """\
{
  "name": "app/app",
  "type": "project",
  "require": {},
  "require-dev": {
    "phpunit/phpunit": "^10.0"
  },
  "scripts": {
    "test": "phpunit"
  }
}
"""

_GO_MOD = """\
module example.com/app

go 1.22
"""

_POM_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>example</groupId>
  <artifactId>app</artifactId>
  <version>0.0.1</version>
  <packaging>jar</packaging>

  <properties>
    <maven.compiler.source>17</maven.compiler.source>
    <maven.compiler.target>17</maven.compiler.target>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
  </properties>
</project>
"""


def _templates_for(toolchain: str) -> list[tuple[str, str]]:
    """Return (relative_path, contents) entries for the given toolchain.

    The first entry is always `.cco.yaml`. Subsequent entries are marker files
    the recipe needs to detect the toolchain. The caller is responsible for
    skipping any marker file that already exists.
    """
    if toolchain == "python":
        return [(".cco.yaml", _CCO_YAML_PYTHON), ("pyproject.toml", _PYPROJECT_TOML)]
    if toolchain == "node":
        return [(".cco.yaml", _CCO_YAML_NODE), ("package.json", _PACKAGE_JSON)]
    if toolchain == "typescript":
        return [
            (".cco.yaml", _CCO_YAML_TYPESCRIPT),
            ("package.json", _PACKAGE_JSON),
            ("tsconfig.json", _TSCONFIG_JSON),
        ]
    if toolchain == "php":
        return [(".cco.yaml", _CCO_YAML_PHP), ("composer.json", _COMPOSER_JSON)]
    if toolchain == "go":
        return [(".cco.yaml", _CCO_YAML_GO), ("go.mod", _GO_MOD)]
    if toolchain == "java":
        return [(".cco.yaml", _CCO_YAML_JAVA), ("pom.xml", _POM_XML)]
    raise ValueError(f"unknown toolchain '{toolchain}'. Supported: {', '.join(SUPPORTED_TOOLCHAINS)}")


def plan_bootstrap(repo_root: Path, toolchain: str) -> BootstrapPlan:
    """Build a BootstrapPlan describing the files that would be written.

    Touches no disk state. The caller decides whether to apply.
    """
    if toolchain not in SUPPORTED_TOOLCHAINS:
        raise ValueError(f"unknown toolchain '{toolchain}'. Supported: {', '.join(SUPPORTED_TOOLCHAINS)}")
    repo_root = Path(repo_root)
    if not repo_root.is_dir():
        raise FileNotFoundError(f"repo-root does not exist: {repo_root}")
    plan = BootstrapPlan(toolchain=toolchain, repo_root=repo_root)
    for rel, contents in _templates_for(toolchain):
        target = repo_root / rel
        if target.exists():
            existing = target.read_text()
            same = existing == contents
            plan.files.append(FileChange(path=target, contents=contents, conflicts=not same, already_present=same))
        else:
            plan.files.append(FileChange(path=target, contents=contents, conflicts=False, already_present=False))
    return plan


def apply_plan(plan: BootstrapPlan, *, force: bool = False) -> list[Path]:
    """Write planned files. Returns the list of paths actually written.

    Files marked `already_present` are skipped. Files marked `conflicts` raise
    unless ``force=True``, in which case they are overwritten.
    """
    conflicts = plan.conflicts
    if conflicts and not force:
        names = ", ".join(str(c.path.relative_to(plan.repo_root)) for c in conflicts)
        raise FileExistsError(
            f"refusing to overwrite existing file(s): {names}. "
            "Pass force=True to overwrite, or remove the file(s) first."
        )
    written: list[Path] = []
    for change in plan.files:
        if change.already_present:
            continue
        change.path.parent.mkdir(parents=True, exist_ok=True)
        change.path.write_text(change.contents)
        written.append(change.path)
    return written


def update_project_standards(project_yaml_path: Path, toolchain: str) -> bool:
    """Append the matching `standards:` entry to project.yaml if missing.

    Returns True iff project.yaml was modified. The write preserves any
    surrounding YAML by appending; we deliberately avoid a full yaml.dump round
    trip so comments and ordering elsewhere in the file survive.
    """
    standards_id = STANDARDS_FOR_TOOLCHAIN.get(toolchain)
    if standards_id is None:
        return False
    if not project_yaml_path.is_file():
        return False
    import yaml  # local import — bootstrap is otherwise stdlib-only

    raw = project_yaml_path.read_text()
    data = yaml.safe_load(raw) or {}
    existing = data.get("standards") or []
    if not isinstance(existing, list):
        return False
    if standards_id in existing:
        return False
    new_list = [*existing, standards_id]
    # Re-emit the standards block in-place if present, else append a fresh one.
    # Use yaml.safe_dump for the block we touch so the value is well-formed,
    # but keep the rest of the file byte-identical when possible.
    if "\nstandards:" in raw or raw.startswith("standards:"):
        data["standards"] = new_list
        project_yaml_path.write_text(yaml.safe_dump(data, sort_keys=False))
    else:
        sep = "" if raw.endswith("\n") else "\n"
        appended = sep + yaml.safe_dump({"standards": new_list}, sort_keys=False)
        project_yaml_path.write_text(raw + appended)
    return True


def commit_changes(repo_root: Path, paths_to_stage: list[Path]) -> str:
    """Stage the given paths and commit with the bootstrap message.

    Returns the new commit's short SHA. Raises ``subprocess.CalledProcessError``
    if git fails (e.g. not a repo, nothing staged after dedup).
    """
    if not paths_to_stage:
        raise ValueError("commit_changes called with empty paths_to_stage")
    rel = [str(p.resolve().relative_to(Path(repo_root).resolve())) for p in paths_to_stage]
    subprocess.run(["git", "-C", str(repo_root), "add", "--", *rel], check=True)
    subprocess.run(
        ["git", "-C", str(repo_root), "commit", "-m", "chore: bootstrap orchestrator project config"],
        check=True,
    )
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def looks_unbootstrapped(repo_root: Path) -> bool:
    """True when neither `.cco.yaml` nor any bundled recipe detects this repo.

    Used at pipeline start to decide whether to warn / offer bootstrap. We
    deliberately do not call into the verifier engine — the goal is a cheap
    detection that does not require importing the full engine module.
    """
    repo_root = Path(repo_root)
    if (repo_root / ".cco.yaml").is_file():
        return False
    try:
        recipes = load_bundled_recipes()
    except FileNotFoundError:
        return True
    return detect_toolchain(repo_root, recipes) is None
