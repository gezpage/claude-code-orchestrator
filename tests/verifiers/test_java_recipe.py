"""Tests for Java verifier recipe detection and command selection."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.verifiers.engine import verify
from orchestrator.verifiers.recipe import load_recipe_by_toolchain

# ---------------------------------------------------------------------------
# Recipe loading
# ---------------------------------------------------------------------------


def test_bundled_java_recipe_loads():
    recipe = load_recipe_by_toolchain("java")
    assert recipe.toolchain == "java"
    assert recipe.priority == 50
    assert "pom.xml" in recipe.any_markers
    assert "build.gradle" in recipe.any_markers
    assert "gradlew" in recipe.any_markers
    assert "mvnw" in recipe.any_markers
    cmd_ids = {c.id for c in recipe.commands}
    assert cmd_ids == {"test_mvnw", "test_maven", "test_gradlew", "test_gradle"}


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _make_run_folder(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    return run


def _completed(code: int) -> MagicMock:
    proc = MagicMock()
    proc.returncode = code
    proc.stdout = ""
    proc.stderr = ""
    return proc


# ---------------------------------------------------------------------------
# Maven detection and command selection
# ---------------------------------------------------------------------------


def test_maven_wrapper_preferred_over_plain_mvn(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>")
    (repo / "mvnw").write_text("#!/bin/sh")

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)

    assert sig["toolchain"] == "java"
    # verify.json carries skipped_reason for test_maven when wrapper is present.
    import json

    report = json.loads((run_folder / "verification" / "verify.json").read_text())
    cmd_map = {c["id"]: c for c in report["commands"]}
    assert cmd_map["test_mvnw"]["status"] == "passed"
    assert cmd_map["test_maven"]["status"] == "skipped"
    assert "mvnw" in (cmd_map["test_maven"].get("skipped_reason") or "")
    # subprocess must be called with the wrapper, not plain mvn.
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("./mvnw test" in c for c in calls)
    assert all("mvn test" not in c for c in calls)


def test_maven_direct_used_when_no_wrapper(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>")
    # No mvnw → plain mvn should run.

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)

    assert sig["toolchain"] == "java"
    import json

    report = json.loads((run_folder / "verification" / "verify.json").read_text())
    cmd_map = {c["id"]: c for c in report["commands"]}
    assert cmd_map["test_maven"]["status"] == "passed"
    assert cmd_map["test_mvnw"]["status"] == "skipped"
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("mvn test" in c for c in calls)


def test_maven_failing_tests_produce_failed_verification(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>")

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)

    assert sig["verification_status"] == "failed"
    assert "test_maven" in sig["failed_command_ids"]


def test_maven_passing_tests_produce_passed_verification(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>")

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)

    assert sig["verification_status"] == "passed"
    assert not sig["failed_command_ids"]


# ---------------------------------------------------------------------------
# Gradle detection and command selection
# ---------------------------------------------------------------------------


def test_gradle_wrapper_preferred_over_plain_gradle(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle").write_text("// gradle")
    (repo / "gradlew").write_text("#!/bin/sh")

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)

    assert sig["toolchain"] == "java"
    import json

    report = json.loads((run_folder / "verification" / "verify.json").read_text())
    cmd_map = {c["id"]: c for c in report["commands"]}
    assert cmd_map["test_gradlew"]["status"] == "passed"
    assert cmd_map["test_gradle"]["status"] == "skipped"
    assert "gradlew" in (cmd_map["test_gradle"].get("skipped_reason") or "")
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("./gradlew test" in c for c in calls)


def test_gradle_direct_used_when_no_wrapper(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle").write_text("// gradle")
    # No gradlew → plain gradle should run.

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)) as mock_run:
        sig = verify(repo, run_folder)

    assert sig["toolchain"] == "java"
    import json

    report = json.loads((run_folder / "verification" / "verify.json").read_text())
    cmd_map = {c["id"]: c for c in report["commands"]}
    assert cmd_map["test_gradle"]["status"] == "passed"
    assert cmd_map["test_gradlew"]["status"] == "skipped"
    calls = [str(c) for c in mock_run.call_args_list]
    assert any("gradle test" in c for c in calls)


def test_kotlin_dsl_build_gradle_kts_triggers_gradle_command(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle.kts").write_text("// kotlin dsl")
    # No gradlew → plain gradle should run.

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)

    assert sig["toolchain"] == "java"
    assert "test_gradle" in sig["command_ids"]


def test_gradle_failing_tests_produce_failed_verification(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "build.gradle").write_text("// gradle")

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(1)):
        sig = verify(repo, run_folder)

    assert sig["verification_status"] == "failed"
    assert "test_gradle" in sig["failed_command_ids"]


# ---------------------------------------------------------------------------
# No applicable command → skipped
# ---------------------------------------------------------------------------


def test_java_project_with_only_settings_gradle_has_no_runnable_command(tmp_path: Path):
    """settings.gradle alone detects Java but produces no runnable test command."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "settings.gradle").write_text("rootProject.name = 'x'")
    # No build.gradle, no gradlew, no pom.xml, no mvnw.

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run") as mock_run:
        sig = verify(repo, run_folder)

    assert sig["toolchain"] == "java"
    # No commands should run — all skipped due to missing prerequisite files.
    assert mock_run.call_count == 0
    # All required commands skipped → not a hard failure.
    assert sig["verification_status"] in {"passed", "warned", "skipped"}


# ---------------------------------------------------------------------------
# verify.json artifact shape
# ---------------------------------------------------------------------------


def test_verify_json_records_expected_fields(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pom.xml").write_text("<project/>")

    run_folder = _make_run_folder(tmp_path)
    with patch("orchestrator.verifiers.engine.subprocess.run", return_value=_completed(0)):
        sig = verify(repo, run_folder)

    import json

    verify_json = json.loads((run_folder / "verification" / "verify.json").read_text())
    assert verify_json["toolchain"] == "java"
    assert isinstance(verify_json["commands"], list)
    cmd = next(c for c in verify_json["commands"] if c["id"] == "test_maven")
    assert "command" in cmd
    assert "status" in cmd
    assert "duration_seconds" in cmd
