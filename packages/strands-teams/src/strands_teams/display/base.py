"""Abstract base class for consuming agent streaming events.

This module defines the ``EventConsumer`` protocol that all consumers must
implement. Consumers receive typed streaming events and can render them in
different ways (Rich terminal, JSON output, web UI, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EventConsumer(ABC):
    """Protocol for consuming agent streaming events.

    Implement this interface to create custom event consumers for different
    output formats (Rich terminal, JSON, web UI, etc.).

    Events follow a lifecycle: run starts -> messages/tools -> run finishes.

    Example:
        >>> class LoggingConsumer(EventConsumer):
        ...     async def on_run_started(self, thread_id: str, run_id: str) -> None:
        ...         print(f"Run started: {run_id}")
    """

    @abstractmethod
    async def on_run_started(self, thread_id: str, run_id: str) -> None:
        """Handle run started event.

        Args:
            thread_id: Unique identifier for the conversation thread.
            run_id: Unique identifier for this agent run.
        """

    @abstractmethod
    async def on_text_start(self, message_id: str, role: str) -> None:
        """Handle start of a text message.

        Args:
            message_id: Unique identifier for the message.
            role: Message role (usually "assistant").
        """

    @abstractmethod
    async def on_text_content(self, message_id: str, delta: str) -> None:
        """Handle streaming text content.

        Args:
            message_id: Identifier of the message being streamed.
            delta: New text chunk to append.
        """

    @abstractmethod
    async def on_text_end(self, message_id: str) -> None:
        """Handle end of a text message.

        Args:
            message_id: Identifier of the completed message.
        """

    @abstractmethod
    async def on_tool_start(self, tool_call_id: str, tool_name: str) -> None:
        """Handle start of tool execution.

        Args:
            tool_call_id: Unique identifier for this tool call.
            tool_name: Name of the tool being called.
        """

    @abstractmethod
    async def on_tool_args(self, tool_call_id: str, args_delta: str) -> None:
        """Handle streaming tool arguments.

        Args:
            tool_call_id: Identifier of the tool call.
            args_delta: JSON string chunk of tool arguments.
        """

    @abstractmethod
    async def on_tool_result(self, tool_call_id: str, result: Any) -> None:
        """Handle tool execution result.

        Args:
            tool_call_id: Identifier of the completed tool call.
            result: Result returned by the tool.
        """

    @abstractmethod
    async def on_tool_end(self, tool_call_id: str) -> None:
        """Handle end of tool execution.

        Args:
            tool_call_id: Identifier of the completed tool call.
        """

    @abstractmethod
    async def on_state_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Handle state snapshot event.

        Args:
            snapshot: Complete state snapshot dictionary.
        """

    @abstractmethod
    async def on_run_finished(self, thread_id: str, run_id: str) -> None:
        """Handle run finished event.

        Args:
            thread_id: Identifier of the conversation thread.
            run_id: Identifier of the completed run.
        """

    @abstractmethod
    async def on_error(self, message: str, code: str | None = None) -> None:
        """Handle error event.

        Args:
            message: Error message description.
            code: Optional error code.
        """

    async def start(self) -> None:
        """Initialize the consumer (optional).

        Override to perform setup before events start streaming.
        Called before the first event is received.
        """

    async def stop(self) -> None:
        """Cleanup the consumer (optional).

        Override to perform cleanup after events stop streaming.
        Called after the last event is received or on error.
        """
