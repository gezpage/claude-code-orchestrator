# Signal extractor; parses the SIGNAL_JSON: sentinel line from Claude stdout into a structured dict.
import json

SENTINEL = "SIGNAL_JSON:"


def extract_signal(stdout: str) -> dict | None:
    # The final sentinel is authoritative; prompt examples can appear earlier in transcripts.
    for line in reversed(stdout.splitlines()):
        stripped = line.strip().strip("`")
        if stripped.startswith(SENTINEL):
            payload = stripped[len(SENTINEL) :].strip()
            try:
                return json.loads(payload)  # type: ignore[no-any-return]
            except json.JSONDecodeError:
                return None
    return None
