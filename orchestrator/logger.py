from datetime import datetime, timezone
from pathlib import Path

_INFO_LEVELS = {"INFO", "ERROR"}


class OrchestratorLogger:
    def __init__(self, run_folder, project_log_path, level="DEBUG"):
        self.run_folder = Path(run_folder)
        self.project_log_path = Path(project_log_path)
        self.level = level.upper()

    def log(self, stage: str, level: str, message: str) -> None:
        level = level.upper()
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        run_id = self.run_folder.name
        line = f"{ts} {run_id} [{level}] [{stage}] {message}\n"

        run_log = self.run_folder / "run.log"
        run_log.parent.mkdir(parents=True, exist_ok=True)
        with run_log.open("a") as f:
            f.write(line)

        if level in _INFO_LEVELS:
            proj_log = self.project_log_path / "orchestrator.log"
            proj_log.parent.mkdir(parents=True, exist_ok=True)
            with proj_log.open("a") as f:
                f.write(line)
