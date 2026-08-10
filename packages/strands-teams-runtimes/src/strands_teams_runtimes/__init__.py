"""strands_teams_runtimes — SpecialistRuntime adapters for autonomous coding on Bedrock.

This package holds the vendor-bearing half of the Phase 3 coding boundary: the
two :class:`~strands_teams.SpecialistRuntime` adapters (:class:`ClaudeAgentRuntime`
for Claude Opus 4.8, :class:`CodexRuntime` for Codex GPT-5.6 Sol), the
:class:`WorkspaceFactory` git-worktree isolation, and the
:func:`make_invoke_coding_agent` tool factory. The coding-agent SDK dependencies
(``claude-agent-sdk``, ``openai-codex``) live only here — ``strands_teams`` core
stays domain- and vendor-free (plan §3).

The typed contracts the adapters satisfy (``SpecialistRuntime``,
``SpecialistResult``, ``ResourceUsage``) are imported from ``strands_teams`` and
re-exported here for convenience.
"""

from __future__ import annotations

from strands_teams import ResourceUsage, SpecialistDisposition, SpecialistResult, SpecialistRuntime

from .claude import CLAUDE_OPUS_MODEL_ID, ClaudeAgentRuntime
from .codex import CODEX_MODEL_ID, CodexRuntime
from .tool import make_invoke_coding_agent
from .workspace import WorkspaceFactory, WorktreeError

__version__ = "3.0.0"

__all__ = [
    "CLAUDE_OPUS_MODEL_ID",
    "CODEX_MODEL_ID",
    "ClaudeAgentRuntime",
    "CodexRuntime",
    "ResourceUsage",
    "SpecialistDisposition",
    "SpecialistResult",
    "SpecialistRuntime",
    "WorkspaceFactory",
    "WorktreeError",
    "__version__",
    "make_invoke_coding_agent",
]
