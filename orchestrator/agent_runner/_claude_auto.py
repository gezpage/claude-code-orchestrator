from __future__ import annotations

import os

from orchestrator.agent_runner._claude import _run_claude_subprocess
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
    auto-discovery are all active. When `sterile_context=True` (the default),
    `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` suppresses ambient auto-memory and
    `--strict-mcp-config --mcp-config '{"mcpServers":{}}'` suppresses every MCP
    server (ADR-023). Callers needing strict reproducibility should prefer
    `codex_cli`.

    Streaming progress events are supported on the same terms as
    ``ClaudeCodePrintRunner`` — see ADR-024.
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
