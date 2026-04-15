"""Shared pytest fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def dummy_transport():
    """Placeholder for a mocked Transport. Real fixture lands in M1 tests."""
    return None
