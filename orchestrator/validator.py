import json
from pathlib import Path
import jsonschema

_SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"


def validate_output(stage: str, data: dict) -> None:
    schema_path = _SCHEMAS_DIR / f"{stage}.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"No schema for stage: {stage}")
    schema = json.loads(schema_path.read_text())
    jsonschema.validate(instance=data, schema=schema)
