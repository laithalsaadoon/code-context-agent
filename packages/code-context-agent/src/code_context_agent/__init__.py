"""Code Context Agent - A CLI tool for code context analysis."""

import os

# Disable OpenTelemetry SDK before any strands imports to avoid
# context detachment errors in async generators.
# This MUST happen before any strands modules are imported.
# Can be overridden by setting CODE_CONTEXT_OTEL_DISABLED=false
if os.environ.get("CODE_CONTEXT_OTEL_DISABLED", "true").lower() != "false":
    os.environ["OTEL_SDK_DISABLED"] = "true"

    # Patch OpenTelemetry context detach to suppress ValueError in async generators.
    # This error occurs when GeneratorExit is raised and the context token was
    # created in a different async context - it's harmless but noisy.
    def _patch_otel_context() -> None:
        try:
            from opentelemetry.context import contextvars_context

            original_detach = contextvars_context.ContextVarsRuntimeContext.detach

            def patched_detach(self, token):
                try:
                    return original_detach(self, token)
                except ValueError:
                    # Suppress "Token was created in a different Context" errors
                    # that occur in async generators during GeneratorExit
                    return None

            contextvars_context.ContextVarsRuntimeContext.detach = patched_detach  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
        except ImportError:
            # opentelemetry not installed — nothing to patch
            pass

    _patch_otel_context()

__version__ = "3.0.0"
__all__ = ["__version__"]
