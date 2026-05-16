# Agent runner abstraction: a backend-agnostic seam for dispatching stage prompts.
# Stages call AgentRunner.run(request) and care only about stdout/exit; the runner
# encapsulates Claude Code / Codex CLI / future backend specifics. See ADR-018.
from orchestrator.agent_runner._claude import ClaudeCodePrintRunner
from orchestrator.agent_runner._claude_auto import ClaudeCodeAutoRunner
from orchestrator.agent_runner._codex import CodexCliRunner
from orchestrator.agent_runner._fake import FakeRunner
from orchestrator.agent_runner._progress import ProgressCallback, ProgressEvent
from orchestrator.agent_runner._protocol import AgentRunner, AgentRunRequest, AgentRunResult
from orchestrator.agent_runner._select import AgentConfig, build_runner, resolve_agent_config

__all__ = [
    "AgentConfig",
    "AgentRunRequest",
    "AgentRunResult",
    "AgentRunner",
    "ClaudeCodeAutoRunner",
    "ClaudeCodePrintRunner",
    "CodexCliRunner",
    "FakeRunner",
    "ProgressCallback",
    "ProgressEvent",
    "build_runner",
    "resolve_agent_config",
]
