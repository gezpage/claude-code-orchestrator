import json

SENTINEL = "SIGNAL_JSON:"


def extract_signal(stdout: str) -> dict | None:
    for line in stdout.splitlines():
        if line.startswith(SENTINEL):
            payload = line[len(SENTINEL):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return None
    return None
