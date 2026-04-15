"""Unified Result[T] return type for all capabilities.

See docs/tool-writing-guide.md §四 and docs/errors.md for conventions.

This is a PLACEHOLDER with the data model only; logic lands in M1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

ErrorCategory = Literal[
    "transport",
    "device",
    "permission",
    "timeout",
    "io",
    "input",
    "system",
    "capability",
]


@dataclass(frozen=True)
class ErrorInfo:
    """Structured error. LLM-friendly: code is enum, suggestion is actionable."""

    code: str
    message: str
    suggestion: str = ""
    category: ErrorCategory = "capability"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "suggestion": self.suggestion,
            "category": self.category,
            "details": self.details,
        }


@dataclass(frozen=True)
class Result(Generic[T]):
    """Canonical return type. See docs/errors.md."""

    ok: bool
    data: T | None = None
    error: ErrorInfo | None = None
    artifacts: list[Path] = field(default_factory=list)
    timing_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "data": self._serialize_data(),
            "error": self.error.to_dict() if self.error else None,
            "artifacts": [str(p) for p in self.artifacts],
            "timing_ms": self.timing_ms,
        }

    def _serialize_data(self) -> Any:
        if self.data is None:
            return None
        if hasattr(self.data, "to_dict"):
            return self.data.to_dict()  # type: ignore[no-any-return]
        if hasattr(self.data, "__dict__"):
            return vars(self.data)
        return self.data


def ok(  # noqa: A001
    data: T | None = None,
    artifacts: list[Path] | None = None,
    timing_ms: int = 0,
) -> Result[T]:
    """Success helper."""
    return Result(
        ok=True,
        data=data,
        error=None,
        artifacts=artifacts or [],
        timing_ms=timing_ms,
    )


def fail(
    code: str,
    message: str = "",
    suggestion: str = "",
    category: ErrorCategory = "capability",
    details: dict[str, Any] | None = None,
    timing_ms: int = 0,
) -> Result[Any]:
    """Failure helper."""
    return Result(
        ok=False,
        data=None,
        error=ErrorInfo(
            code=code,
            message=message or code,
            suggestion=suggestion,
            category=category,
            details=details or {},
        ),
        artifacts=[],
        timing_ms=timing_ms,
    )
