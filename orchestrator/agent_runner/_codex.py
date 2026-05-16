from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class CodexCliRunner(AgentRunner):
    """Dispatch via `codex exec` non-interactive mode.

    Defaults to `--sandbox workspace-write` — the least-permissive sandbox that is
    still useful for stage work (sandboxed FS writes, no network egress, no host
    access). `permission_mode: danger-full-access` lifts the filesystem sandbox so
    the agent can write `.git/` (needed when a stage must commit). The explicit
    `full-auto` alias maps to `--dangerously-bypass-approvals-and-sandbox` (the
    current Codex flag for "no sandbox, no approvals") and is never the default.

    `-c approval_policy=never` is also passed in every non-`full-auto` branch:
    codex has two independent gates (sandbox + approval), and the sandbox flag
    alone is not sufficient for unattended runs. Without `never`, codex escalates
    out-of-workspace writes (and other sandbox-allowed-but-flagged operations) to
    a human, which deadlocks `codex exec` and surfaces as `error=patch rejected:
    rejected by user approval settings`. The approval policy is set via the
    `-c key=value` config override flag because `codex exec` (the non-interactive
    subcommand) does not expose `--ask-for-approval` directly — that flag only
    exists on the top-level interactive `codex` command. `full-auto` already
    implies "no approval" via `--dangerously-bypass-approvals-and-sandbox`, so it
    does not receive the override.

    `sterile_context` is currently a no-op for this backend — codex has no
    equivalent of CLAUDE_CODE_DISABLE_AUTO_MEMORY, but the constructor accepts the
    flag so the selector can pass it uniformly.
    """

    backend_name = "codex_cli"

    _SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})
    _DEFAULT_SANDBOX = "workspace-write"

    def __init__(
        self,
        *,
        sterile_context: bool = True,
        model: str | None = None,
        timeout_seconds: int | None = None,
        permission_mode: str | None = None,
        output_mode: str = "text",
    ) -> None:
        self._sterile_context = sterile_context
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._permission_mode = permission_mode
        self._output_mode = output_mode

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        cmd = ["codex", "exec", request.prompt]
        mode = (request.permission_mode or self._permission_mode or "").replace("_", "-")
        if mode == "full-auto":
            # Codex CLI replaced --full-auto with --dangerously-bypass-approvals-and-sandbox.
            cmd += ["--dangerously-bypass-approvals-and-sandbox"]
        elif mode in self._SANDBOX_MODES:
            cmd += ["--sandbox", mode, "-c", "approval_policy=never"]
        else:
            cmd += ["--sandbox", self._DEFAULT_SANDBOX, "-c", "approval_policy=never"]
        model = request.model or self._model
        timeout_seconds = request.timeout_seconds if request.timeout_seconds is not None else self._timeout_seconds
        workspace_root = request.workspace_root or request.cwd
        if workspace_root:
            cmd += ["--cd", workspace_root, "--skip-git-repo-check"]
        seen_roots = {str(Path(workspace_root).resolve())} if workspace_root else set()
        for root in request.writable_roots:
            resolved = str(Path(root).resolve())
            if resolved in seen_roots:
                continue
            seen_roots.add(resolved)
            cmd += ["--add-dir", root]
        if model:
            cmd += ["-m", model]

        env = os.environ.copy()
        if request.env:
            env.update(request.env)

        t0 = time.monotonic()
        timed_out = False
        exit_code: int | None = None
        chunks: list[str] = []

        with tempfile.NamedTemporaryFile(prefix="orch-codex-last-", suffix=".md", delete=False) as tmp:
            last_message_path = Path(tmp.name)
        cmd += ["--output-last-message", str(last_message_path)]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=workspace_root or request.cwd,
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
            last_message = last_message_path.read_text() if last_message_path.exists() else ""
        finally:
            try:
                last_message_path.unlink()
            except FileNotFoundError:
                pass

        # Prefer the clean final agent message captured by --output-last-message
        # for in-memory result.stdout (signal-JSON parsing consumes this) — it
        # strips the Codex banner, workdir/model/sandbox metadata, prompt echo,
        # command logs, diffs and token accounting.
        result_stdout = last_message if last_message.strip() else stdout

        return AgentRunResult(
            backend=self.backend_name,
            stdout=result_stdout,
            stderr="",
            exit_code=exit_code,
            duration_seconds=duration,
            timed_out=timed_out,
            command=cmd,
        )
