from __future__ import annotations

from collections.abc import Callable

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class FakeRunner(AgentRunner):
    """Test double. Records every request; returns canned stdout per call.

    Pass either a single string (returned for every call) or a list/iterable of
    strings consumed one per call. `exit_code` and `timed_out` accept a scalar
    (used for every call) or a list (consumed per call alongside `stdout`).
    Optionally pass a `responder` callable for request-aware test logic.
    """

    backend_name = "fake"

    def __init__(
        self,
        stdout: str | list[str] | None = None,
        *,
        responder: Callable[[AgentRunRequest], str] | None = None,
        exit_code: int | list[int] = 0,
        timed_out: bool | list[bool] = False,
    ) -> None:
        if stdout is None and responder is None:
            stdout = ""
        self._responder = responder
        self._stdouts: list[str] = []
        if stdout is not None:
            self._stdouts = [stdout] if isinstance(stdout, str) else list(stdout)
        self._idx = 0
        self._exit_codes: list[int] = [exit_code] if isinstance(exit_code, int) else list(exit_code)
        self._timed_outs: list[bool] = [timed_out] if isinstance(timed_out, bool) else list(timed_out)
        self.requests: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        i = self._idx
        self.requests.append(request)
        if self._responder is not None:
            out = self._responder(request)
        elif self._stdouts:
            out = self._stdouts[min(i, len(self._stdouts) - 1)]
        else:
            out = ""
        exit_code = self._exit_codes[min(i, len(self._exit_codes) - 1)]
        timed_out = self._timed_outs[min(i, len(self._timed_outs) - 1)]
        self._idx += 1

        if request.stream_log_path is not None:
            request.stream_log_path.parent.mkdir(parents=True, exist_ok=True)
            request.stream_log_path.write_text(out)

        return AgentRunResult(
            backend=self.backend_name,
            stdout=out,
            stderr="",
            exit_code=exit_code,
            duration_seconds=0.0,
            timed_out=timed_out,
            stream_log_path=request.stream_log_path,
            command=None,
        )
