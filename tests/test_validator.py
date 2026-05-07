import pytest
import jsonschema
from orchestrator.validator import validate_output


def test_valid_pass():
    data = {"stage": "discovery", "status": "passed", "findings_files": ["path/to/findings.md"]}
    validate_output("discovery", data)  # should not raise


def test_valid_blocked():
    data = {"stage": "discovery", "status": "blocked", "message": "Could not find overview"}
    validate_output("discovery", data)  # should not raise


def test_missing_required_field():
    data = {"stage": "discovery"}  # missing "status"
    with pytest.raises(jsonschema.ValidationError):
        validate_output("discovery", data)


def test_extra_fields_allowed():
    data = {
        "stage": "discovery",
        "status": "passed",
        "findings_files": [],
        "extra_custom_field": "some value",
        "another_extra": 42,
    }
    validate_output("discovery", data)  # extra fields must not raise


def test_qa_output_valid():
    data = {"stage": "qa", "status": "passed", "outcome": "pass", "confidence": "high", "regression_risk": "low"}
    validate_output("qa", data)


def test_review_output_valid():
    data = {
        "stage": "review",
        "status": "passed",
        "reviewer_statuses": {"architecture": "approved"},
        "changes_requested": [],
    }
    validate_output("review", data)


def test_unknown_stage_raises():
    with pytest.raises(FileNotFoundError):
        validate_output("nonexistent-stage", {"stage": "x", "status": "passed"})
