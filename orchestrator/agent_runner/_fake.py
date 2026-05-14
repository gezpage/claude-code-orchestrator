from __future__ import annotations

from collections.abc import Callable

from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult


class FakeRunner(AgentRunner):
    """Test double. Records every request; returns canned stdout per call.

    Pass either a single string (returned for every call) or a list/iterable of
    strings consumed one per call. Optionally pass a `responder` callable for
    request-aware test logic.
    """

    backend_name = "fake"

    def __init__(
        self,
        stdout: str | list[str] | None = None,
        *,
        responder: Callable[[AgentRunRequest], str] | None = None,
        exit_code: int = 0,
    ) -> None:
        if stdout is None and responder is None:
            stdout = ""
        self._responder = responder
        self._stdouts: list[str] = []
        if stdout is not None:
            self._stdouts = [stdout] if isinstance(stdout, str) else list(stdout)
        self._idx = 0
        self._exit_code = exit_code
        self.requests: list[AgentRunRequest] = []

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        self.requests.append(request)
        if self._responder is not None:
            out = self._responder(request)
        elif self._stdouts:
            i = min(self._idx, len(self._stdouts) - 1)
            out = self._stdouts[i]
            self._idx += 1
        else:
            out = ""

        if request.transcript_path is not None:
            request.transcript_path.parent.mkdir(parents=True, exist_ok=True)
            request.transcript_path.write_text(out)

        return AgentRunResult(
            backend=self.backend_name,
            stdout=out,
            stderr="",
            exit_code=self._exit_code,
            duration_seconds=0.0,
            timed_out=False,
            transcript_path=request.transcript_path,
            command=None,
        )
