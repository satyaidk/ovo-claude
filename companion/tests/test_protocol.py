"""The device message: field names, limits and allowed values must match
firmware/MochiEyes/src/claude/ClaudeStatus.h."""
import json
import pathlib
import re

import pytest

from ovo_companion.protocol import (
    EFFORT_LEVELS, LIMITS, MAX_BODY, PERMISSION_LABELS, PERMISSION_MODES, STATES, Snapshot, ascii_text,
    build_payload, encode, ident, normalize_effort, normalize_permission,
)

FIRMWARE = pathlib.Path(__file__).resolve().parents[2] / "firmware" / "MochiEyes" / "src" / "claude" / "ClaudeStatus.h"


def test_payload_has_every_field_the_firmware_reads():
    p = build_payload(Snapshot(state="running", session_id="3f2a9c1e", sessions=2, event="PreToolUse",
                               model="Opus", effort="xhigh", activity="Running tests",
                               permission_mode="default", elapsed_seconds=763, session_seconds=900),
                      cid="a1b2c3d4", seq=7)
    assert p == {
        "v": 1, "seq": 7, "cid": "a1b2c3d4", "connected": True, "state": "running", "session_id": "3f2a9c1e",
        "sessions": 2, "event": "PreToolUse", "model": "Opus", "effort": "xhigh", "activity": "Running tests",
        "permission_mode": "default", "approval_required": False, "elapsed_seconds": 763, "session_seconds": 900,
    }
    source = FIRMWARE.read_text(encoding="utf-8")
    for key in p:
        assert f'"{key}"' in source, f"firmware does not read {key}"


def test_limits_match_the_firmware():
    source = FIRMWARE.read_text(encoding="utf-8")
    fw = {name: int(n) for name, n in re.findall(r"k(\w+)Len\s*=\s*(\d+);", source)}
    assert fw["Cid"] == LIMITS["cid"]
    assert fw["Session"] == LIMITS["session_id"]
    assert fw["Event"] == LIMITS["event"]
    assert fw["Model"] == LIMITS["model"]
    assert fw["Activity"] == LIMITS["activity"]
    assert f"kMaxBody        = {MAX_BODY}" in source
    for state in STATES:
        assert f'"{state}"' in source
    for level in EFFORT_LEVELS:
        assert f'"{level}"' in source


def test_text_is_printable_ascii_and_cut_with_a_marker():
    assert ascii_text("café ✓", 32) == "caf? ?"
    assert ascii_text("line1\nline2\t x", 32) == "line1 line2 x"
    assert ascii_text("a" * 40, 32) == "a" * 31 + "."
    assert ascii_text(None, 5) == ""


def test_ids_keep_only_safe_characters():
    assert ident("3f2a-9c1e_x y!", 16) == "3f2a-9c1e_xy"
    assert ident("a" * 30, 16) == "a" * 16


def test_unknown_permission_mode_is_other_never_auto():
    for mode in PERMISSION_MODES:
        assert normalize_permission(mode) == mode
    assert normalize_permission("yolo") == "other"
    assert normalize_permission(None) == ""
    assert PERMISSION_LABELS["auto"] == "AUTO"
    assert [m for m, label in PERMISSION_LABELS.items() if label == "AUTO"] == ["auto"]
    assert PERMISSION_LABELS["default"] == "MANUAL"


def test_effort_only_documented_levels():
    for level in ("low", "medium", "high", "xhigh", "max"):
        assert normalize_effort(level) == level
    for bad in ("ultra", "HIGH", "", None, 3):
        assert normalize_effort(bad) is None
    p = build_payload(Snapshot(state="running", effort="ultra"), "c1", 1)
    assert p["effort"] == ""


def test_approval_state_sets_the_flag_and_seconds_are_clamped():
    p = build_payload(Snapshot(state="approval", elapsed_seconds=-5, session_seconds=10 ** 9), "c1", 1)
    assert p["approval_required"] is True
    assert p["elapsed_seconds"] == 0
    assert p["session_seconds"] == 30 * 24 * 3600


def test_worst_case_payload_fits_the_device_buffer():
    snap = Snapshot(state="turn_done", session_id="x" * 99, sessions=500, event="E" * 99, model="M" * 99,
                    effort="medium", activity="A" * 99, permission_mode="bypassPermissions",
                    approval_required=True, elapsed_seconds=10 ** 9, session_seconds=10 ** 9)
    body = encode(build_payload(snap, "c" * 99, 2 ** 40))
    assert len(body) <= MAX_BODY
    data = json.loads(body)
    assert data["sessions"] == 99 and data["seq"] < 2 ** 32


def test_bad_state_and_empty_cid_are_refused():
    with pytest.raises(ValueError):
        build_payload(Snapshot(state="thinking"), "c1", 1)
    with pytest.raises(ValueError):
        build_payload(Snapshot(state="idle"), "!!", 1)
