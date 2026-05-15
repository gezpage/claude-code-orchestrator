from __future__ import annotations

import os
import subprocess
import time

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class ClaudeCodeAutoRunner(AgentRunner):
    """Dispatch via the `claude` CLI with `--permission-mode auto`.

    Same OAuth/keychain auth path as `ClaudeCodePrintRunner` (see ADR-022):
    `-p` and `--bare` are absent, and `ANTHROPIC_API_KEY` /
    `ANTHROPIC_AUTH_TOKEN` are stripped from the forwarded env so a stale
    external key cannot override keychain auth.

    The only difference from `claude_code_print` is permission handling:
    `--permission-mode auto` keeps Claude's permission system engaged
    (next-most-permissive short of `bypassPermissions`) instead of using
    `--dangerously-skip-permissions`. Both runners share the same isolation
    profile — hooks, LSP, plugin sync, keychain reads and CLAUDE.md
    auto-discovery are all active. `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` is set
    by default (sterile_context) so ambient auto-memory is suppressed; callers
    needing strict reproducibility should prefer `codex_cli`.
    """

    backend_name = "claude_code_auto"

    def __init__(
        self,
        *,
        sterile_context: bool = True,
        model: str | None = None,
        timeout_seconds: int | None = None,
        output_mode: str = "text",
    ) -> None:
        self._sterile_context = sterile_context
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._output_mode = output_mode

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        cmd = ["claude", request.prompt, "--permission-mode", "auto"]
        model = request.model or self._model
        output_mode = request.output_mode if request.output_mode != "text" else self._output_mode
        timeout_seconds = request.timeout_seconds if request.timeout_seconds is not None else self._timeout_seconds
        if model:
            cmd += ["--model", model]
        if output_mode and output_mode != "text":
            cmd += ["--output-format", output_mode]

        env = os.environ.copy()
        # Force keychain/OAuth auth — see ADR-022.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        if self._sterile_context:
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
                exit_code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                timed_out = True
        finally:
            duration = time.monotonic() - t0

        stdout = "".join(chunks)

        return AgentRunResult(
            backend=self.backend_name,
            stdout=stdout,
            stderr="",
            exit_code=exit_code,
            duration_seconds=duration,
            timed_out=timed_out,
            command=cmd,
        )
