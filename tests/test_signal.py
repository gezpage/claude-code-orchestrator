from orchestrator.signal import extract_signal


def test_valid_sentinel():
    stdout = 'SIGNAL_JSON: {"stage": "discovery", "status": "passed"}'
    result = extract_signal(stdout)
    assert result == {"stage": "discovery", "status": "passed"}


def test_sentinel_buried_in_prose():
    stdout = 'Some reasoning here\nMore text\nSIGNAL_JSON: {"stage": "qa", "status": "passed"}\nTrailing text'
    result = extract_signal(stdout)
    assert result["stage"] == "qa"
    assert result["status"] == "passed"


def test_no_sentinel():
    stdout = "No signal here\nJust prose output"
    result = extract_signal(stdout)
    assert result is None


def test_malformed_json_after_prefix():
    stdout = "SIGNAL_JSON: {not valid json"
    result = extract_signal(stdout)
    assert result is None


def test_empty_stdout():
    result = extract_signal("")
    assert result is None
