# Signal extractor; parses the SIGNAL_JSON: sentinel line from Claude stdout into a structured dict.
import json

SENTINEL = "SIGNAL_JSON:"


def extract_signal(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        stripped = line.strip().strip("`")
        if stripped.startswith(SENTINEL):
            payload = stripped[len(SENTINEL):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return None
    return None
