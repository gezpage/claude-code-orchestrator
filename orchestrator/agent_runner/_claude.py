from __future__ import annotations

import os
import subprocess
import time

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class ClaudeCodePrintRunner(AgentRunner):
    """Dispatch via the `claude` CLI for unattended stage runs.

    `--dangerously-skip-permissions` is mandatory (ADR-003) — stage agents cannot
    pause on permission prompts. `-p` and `--bare` are intentionally absent: see
    ADR-022. `--bare` forces `ANTHROPIC_API_KEY`-only auth, which excludes OAuth /
    keychain logins; `-p` is unnecessary because subprocess-piped stdout already
    triggers Claude Code's non-interactive mode. `ANTHROPIC_API_KEY` /
    `ANTHROPIC_AUTH_TOKEN` are stripped from the forwarded env so a stale or
    invalid external key in the caller's shell never overrides the user's
    keychain auth.

    When `sterile_context=True` (the default), `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
    suppresses ambient auto-memory and `--strict-mcp-config --mcp-config
    '{"mcpServers":{}}'` suppresses every MCP server the user has configured
    globally or in `.mcp.json`. See ADR-023.
    """

    backend_name = "claude_code_print"

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
        cmd = ["claude", request.prompt, "--dangerously-skip-permissions"]
        model = request.model or self._model
        output_mode = request.output_mode if request.output_mode != "text" else self._output_mode
        timeout_seconds = request.timeout_seconds if request.timeout_seconds is not None else self._timeout_seconds
        if model:
            cmd += ["--model", model]
        if output_mode and output_mode != "text":
            cmd += ["--output-format", output_mode]
        if self._sterile_context:
            # ADR-023: suppress every globally / project-configured MCP server.
            cmd += ["--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}']

        env = os.environ.copy()
        # Force keychain/OAuth auth — see ADR-022.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
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
