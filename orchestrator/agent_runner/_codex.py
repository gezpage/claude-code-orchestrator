from __future__ import annotations

import os
import subprocess
import time

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class CodexCliRunner(AgentRunner):
    """Dispatch via `codex exec` non-interactive mode.

    Defaults to `--full-auto` (no permission prompts). If `permission_mode` is set to
    a recognised codex sandbox label, that maps to `--sandbox <label>` instead.
    `sterile_context` is currently a no-op for this backend — codex has no equivalent
    of CLAUDE_CODE_DISABLE_AUTO_MEMORY, but the constructor accepts the flag so the
    selector can pass it uniformly.
    """

    backend_name = "codex_cli"

    _SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})

    def __init__(self, *, sterile_context: bool = True) -> None:
        self._sterile_context = sterile_context

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        cmd = ["codex", "exec", request.prompt]
        mode = (request.permission_mode or "").replace("_", "-")
        if mode in self._SANDBOX_MODES:
            cmd += ["--sandbox", mode]
        else:
            cmd += ["--full-auto"]
        if request.model:
            cmd += ["-m", request.model]

        env = os.environ.copy()
        if request.env:
            env.update(request.env)

        t0 = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        chunks: list[str] = []

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=request.cwd,
            env=env,
        )
        try:
            if proc.stdout is None:
                raise RuntimeError("Popen stdout is None — subprocess was not opened with PIPE")
            for line in proc.stdout:
                print(line, end="", flush=True)  # noqa: T201
                chunks.append(line)
            try:
                exit_code = proc.wait(timeout=request.timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True
        finally:
            duration = time.monotonic() - t0

        stdout = "".join(chunks)
        if request.transcript_path is not None:
            request.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            request.transcript_path.write_text(stdout)

        return AgentRunResult(
            backend=self.backend_name,
            stdout=stdout,
            stderr="",
            exit_code=exit_code,
            duration_seconds=duration,
            timed_out=timed_out,
            transcript_path=request.transcript_path,
            command=cmd,
        )
