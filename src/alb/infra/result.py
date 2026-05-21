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


# ─── REST envelope helpers (architecture.md "REST envelope 三态约定") ──
def envelope_dict(r: Result[Any]) -> dict[str, Any]:
    """Build a REST envelope from a :class:`Result`.

    Returned shape (matches the inline pattern that grew across
    ``power_route`` / ``app_route`` / ``diag_route`` / ``log_search_route``
    — extracted here to keep all 5 new routes on one source of truth):

    * ok branch:  ``{"ok": True,  "data": <to_dict()ed>, "timing_ms": N}``
    * err branch: ``{"ok": False, "error": <ErrorInfo dict>, "timing_ms": N}``

    ``artifacts`` is intentionally omitted (the routes that produce
    artefacts already encode the path inside ``data``).  Use
    :meth:`Result.to_dict` directly when ``artifacts`` matters.
    """
    if not r.ok:
        return {
            "ok": False,
            "error": r.error.to_dict() if r.error else None,
            "timing_ms": r.timing_ms,
        }
    data: Any
    if r.data is None:
        data = None
    elif hasattr(r.data, "to_dict"):
        data = r.data.to_dict()
    else:
        data = r.data
    return {"ok": True, "data": data, "timing_ms": r.timing_ms}


def envelope_transport_init_error(
    exc: BaseException, **extra: Any
) -> dict[str, Any]:
    """200 + ``ok=false`` envelope for a ``build_transport`` failure.

    Per ``architecture.md`` "REST envelope 三态约定" (b), transport init
    failure is **device-side** / upstream — it must surface as an
    envelope, NOT as ``HTTPException(503)``.  Routes that need to
    short-circuit on transport init failure use this helper plus a
    tuple-return ``_resolve_transport`` so the front-end sees the
    canonical envelope shape on both ``isSuccess`` and ``ok=false``.

    Extra fields (``serial=``, ``device=``, etc.) propagate to the
    envelope for parity with ``devices_route.device_screenshot``.
    """
    return {
        "ok": False,
        "transport": None,
        "error": {
            "code": "TRANSPORT_INIT_FAILED",
            "message": f"{type(exc).__name__}: {exc}",
            "suggestion": (
                "Check device connectivity / adb server / serial bridge"
            ),
            "category": "transport",
            "details": {},
        },
        **extra,
    }
