"""FastAPI server entry point (`alb-api` command).

M0 skeleton. Real API lands in M2.
"""

from __future__ import annotations

import sys


def main() -> None:
    """Entry point referenced by pyproject.toml `[project.scripts]`."""
    print(
        "[alb-api] M0 skeleton — not implemented yet.\n"
        "M2 will ship a FastAPI server with full OpenAPI + WebSocket streaming.\n"
        "See docs/project-plan.md.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
