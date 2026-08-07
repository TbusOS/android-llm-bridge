"""`alb flash` failure rendering (ADR-056).

The bug these lock (seen on real hardware 2026-08-07): the agent reported
`FASTBOOT_NO_DEVICE` — exactly the right code — and the CLI printed
"fastboot failed" next to it. The code was right and the message pointed
nowhere, which leaves the reader in the same place as a code pointing at the
wrong subsystem: go find out yourself.
"""

from __future__ import annotations

import pytest

from alb.cli.flash_cli import _explain


def test_generic_tool_wording_is_replaced_by_the_catalog_sentence():
    message, advice = _explain("FASTBOOT_NO_DEVICE", "fastboot failed")
    assert "fastboot failed" not in message
    assert "No device is visible to fastboot" in message
    assert "fastboot" in advice and advice  # a remedy, not just a restatement


def test_a_specific_message_from_the_agent_wins():
    """When the agent DID have something to say, do not overwrite it — it
    saw the failure and this side did not."""
    message, advice = _explain("FLASH_FAILED", "FAILED (remote: 'partition table doesn't exist')")
    assert "partition table" in message
    assert advice  # the remedy still comes from the catalog


@pytest.mark.parametrize("blank", ["", "   ", "unknown", "failed", "error"])
def test_empty_ish_messages_fall_back(blank):
    message, _ = _explain("FASTBOOT_BUSY", blank)
    assert "Another flash job" in message


def test_unknown_code_does_not_invent_advice():
    message, advice = _explain("NOT_A_REAL_CODE", "something went sideways")
    assert message == "something went sideways"
    assert advice == ""


def test_no_code_and_no_message_still_says_something():
    message, advice = _explain("", "")
    assert message == "unknown failure"
    assert advice == ""


@pytest.mark.parametrize(
    "code",
    [
        "FASTBOOT_UNAVAILABLE",
        "FASTBOOT_BUSY",
        "FASTBOOT_NO_DEVICE",
        "FLASH_IMAGE_CORRUPT",
        "FLASH_PARTITION_REJECTED",
        "FLASH_FAILED",
    ],
)
def test_every_flash_code_carries_a_remedy(code):
    """A code with no advice is half a diagnosis. Locks the catalog too, not
    just the renderer."""
    message, advice = _explain(code, "")
    assert message and advice
    assert advice != message
