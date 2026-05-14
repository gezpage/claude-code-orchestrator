from __future__ import annotations

import os
import subprocess
import time

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class ClaudeCodePrintRunner(AgentRunner):
    """Dispatch via `claude -p` print mode.

    `--bare` and `--dangerously-skip-permissions` are mandatory on every invocation —
    they are the original ADR-003 / ADR-012 invariants, now scoped to this runner.
    Stage agents have no MCP/hook access and bypass permission prompts unattended.
    """

    backend_name = "claude_code_print"

    def __init__(self, *, sterile_context: bool = True) -> None:
        self._sterile_context = sterile_context

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        cmd = ["claude", "-p", request.prompt, "--bare", "--dangerously-skip-permissions"]
        if request.model:
            cmd += ["--model", request.model]
        if request.output_mode and request.output_mode != "text":
            cmd += ["--output-format", request.output_mode]

        env = os.environ.copy()
        if self._sterile_context:
            # Suppress ambient auto-memory injection so pipeline runs are reproducible.
            env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
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
