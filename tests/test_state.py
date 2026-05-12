from orchestrator.state import load_state, save_state, update_stage_status


def test_load_absent_file(tmp_path):
    result = load_state(tmp_path)
    assert result == {}


def test_round_trip(tmp_path):
    state = {"stages": {"discovery": "passed"}, "blocked_at": None}
    save_state(tmp_path, state)
    loaded = load_state(tmp_path)
    assert loaded == state


def test_update_stage_status(tmp_path):
    update_stage_status(tmp_path, "discovery", "passed")
    state = load_state(tmp_path)
    assert state["stages"]["discovery"] == "passed"


def test_update_stage_status_multiple(tmp_path):
    update_stage_status(tmp_path, "discovery", "passed")
    update_stage_status(tmp_path, "specification", "blocked")
    state = load_state(tmp_path)
    assert state["stages"]["discovery"] == "passed"
    assert state["stages"]["specification"] == "blocked"


def test_no_cr_fields(tmp_path):
    save_state(tmp_path, {"stages": {"discovery": "passed"}})
    state = load_state(tmp_path)
    assert "cr_ref" not in state
    assert "done_signal" not in state
    assert "awaiting-review" not in state
