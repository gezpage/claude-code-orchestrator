from __future__ import annotations

import os
import subprocess
import time

from orchestrator.agent_runner._progress import (
    extract_claude_final_text,
    parse_claude_stream_line,
)
from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class ClaudeCodeRunner(AgentRunner):
    """Dispatch via the `claude` CLI with `--permission-mode auto`.

    `--dangerously-skip-permissions` is intentionally absent — see ADR-025,
    which supersedes ADR-003. `--permission-mode auto` is the next-most-
    permissive mode short of `bypassPermissions` and is sufficient for
    unattended pipeline dispatch in practice while keeping Claude's permission
    system engaged. `-p` and `--bare` are also absent (ADR-022): `--bare`
    forces `ANTHROPIC_API_KEY`-only auth, which excludes OAuth / keychain
    logins; `-p` is unnecessary because subprocess-piped stdout already
    triggers Claude Code's non-interactive mode. `ANTHROPIC_API_KEY` /
    `ANTHROPIC_AUTH_TOKEN` are stripped from the forwarded env so a stale or
    invalid external key in the caller's shell never overrides the user's
    keychain auth.

    When `sterile_context=True` (the default), `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`
    suppresses ambient auto-memory and `--strict-mcp-config --mcp-config
    '{"mcpServers":{}}'` suppresses every MCP server the user has configured
    globally or in `.mcp.json`. See ADR-023. Hooks, LSP, plugin sync,
    keychain reads and `CLAUDE.md` auto-discovery remain active; callers
    needing strict reproducibility should prefer `codex_cli`.

    When the request carries a ``progress_callback`` the runner switches the
    CLI to ``--output-format stream-json --verbose`` and forwards each parsed
    event to the callback. ``result.stdout`` is reconstructed as the agent's
    final message (the ``result`` event's ``result`` field) so SIGNAL_JSON
    extraction is unaffected. See ADR-024.
    """

    backend_name = "claude_code"

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
        timeout_seconds = request.timeout_seconds if request.timeout_seconds is not None else self._timeout_seconds
        requested_output_mode = request.output_mode if request.output_mode != "text" else self._output_mode
        streaming = request.progress_callback is not None or requested_output_mode == "stream-json"
        effective_output_mode = "stream-json" if streaming else requested_output_mode
        if model:
            cmd += ["--model", model]
        if effective_output_mode and effective_output_mode != "text":
            cmd += ["--output-format", effective_output_mode]
        if streaming:
            cmd += ["--verbose"]
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

        return _run_claude_subprocess(
            cmd=cmd,
            env=env,
            cwd=request.cwd,
            timeout_seconds=timeout_seconds,
            streaming=streaming,
            progress_callback=request.progress_callback,
            backend_name=self.backend_name,
        )


def _run_claude_subprocess(
    *,
    cmd: list[str],
    env: dict[str, str],
    cwd: str | None,
    timeout_seconds: int | None,
    streaming: bool,
    progress_callback,
    backend_name: str,
) -> AgentRunResult:
    """Subprocess driver for the Claude runner.

    In ``streaming`` mode the runner expects JSONL output from claude: each line is
    parsed into ``ProgressEvent``s (forwarded to ``progress_callback``) and the
    final ``result`` event's text becomes ``result.stdout`` so signal extraction
    keeps working over the clean agent reply rather than the JSON envelope. In
    text mode behaviour is unchanged from before — every line is tee'd to the
    parent stdout and concatenated into ``result.stdout``.
    """
    t0 = time.monotonic()
    timed_out = False
    exit_code: int | None = None
    raw_chunks: list[str] = []
    final_text: str | None = None

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=cwd,
        env=env,
    )
    try:
        if proc.stdout is None:
            raise RuntimeError("Popen stdout is None — subprocess was not opened with PIPE")
        for line in proc.stdout:
            raw_chunks.append(line)
            if streaming:
                if progress_callback is not None:
                    for event in parse_claude_stream_line(line):
                        try:
                            progress_callback(event)
                        except Exception:  # noqa: S110 — callback errors must not abort a stage
                            pass
                maybe_final = extract_claude_final_text(line)
                if maybe_final is not None:
                    final_text = maybe_final
            else:
                print(line, end="", flush=True)  # noqa: T201
        try:
            exit_code = proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            timed_out = True
    finally:
        duration = time.monotonic() - t0

    raw_stdout = "".join(raw_chunks)
    if streaming:
        # Prefer the agent's final message for downstream parsing; fall back to the
        # raw JSONL stream so noisy failures (auth errors, banner-only output, etc.)
        # remain diagnosable in *-output.md.
        stdout = final_text if final_text is not None and final_text.strip() else raw_stdout
    else:
        stdout = raw_stdout

    return AgentRunResult(
        backend=backend_name,
        stdout=stdout,
        stderr="",
        exit_code=exit_code,
        duration_seconds=duration,
        timed_out=timed_out,
        command=cmd,
    )
