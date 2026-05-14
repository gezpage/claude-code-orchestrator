from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class CodexCliRunner(AgentRunner):
    """Dispatch via `codex exec` non-interactive mode.

    Defaults to `--sandbox workspace-write` — the most permissive sandbox that still
    blocks arbitrary network egress and writes outside the repo. `--full-auto` is
    available by setting `permission_mode: danger-full-access` (or the explicit
    `full-auto` alias) but is never the default for a freshly added backend.
    `sterile_context` is currently a no-op for this backend — codex has no equivalent
    of CLAUDE_CODE_DISABLE_AUTO_MEMORY, but the constructor accepts the flag so the
    selector can pass it uniformly.
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
            cmd += ["--full-auto"]
        elif mode in self._SANDBOX_MODES:
            cmd += ["--sandbox", mode]
        else:
            cmd += ["--sandbox", self._DEFAULT_SANDBOX]
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

        if request.transcript_path is not None:
            request.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            request.transcript_path.write_text(stdout)
        result_stdout = last_message if last_message.strip() else stdout

        return AgentRunResult(
            backend=self.backend_name,
            stdout=result_stdout,
            stderr="",
            exit_code=exit_code,
            duration_seconds=duration,
            timed_out=timed_out,
            transcript_path=request.transcript_path,
            command=cmd,
        )
