"""The kiosk's screen resolution: which setting values are valid, and how
system/scripts/apply-screen-resolution.py turns one into a mode the display
really supports (and reports it, so Settings can say when it had to settle)."""
import asyncio
import importlib.machinery
import importlib.util
import json
import os

import pytest
from fastapi import HTTPException

import main
import pairing
import system_control

ROOT = os.environ.get("PLATFORM_ROOT")
needs_script = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")


def load_script():
    loader = importlib.machinery.SourceFileLoader("apply_screen_resolution", f"{ROOT}/system/scripts/apply-screen-resolution.py")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


script = load_script() if ROOT else None


def wlr_text(name, modes, label="Make Model"):
    """wlr-randr's text output for one output; modes = [(w, h, hz, flags)]."""
    lines = [f'{name} "{label} ({name})"', "  Make: Make", "  Enabled: yes", "  Modes:"]
    for w, h, hz, flags in modes:
        lines.append(f"    {w}x{h} px, {hz:.6f} Hz" + (f" ({flags})" if flags else ""))
    lines += ["  Position: 0,0", "  Transform: normal", "  Scale: 1.000000"]
    return "\n".join(lines) + "\n"


UHD_TV = [
    (3840, 2160, 30.0, "preferred"),
    (3840, 2160, 29.97, ""),
    (1920, 1080, 60.0, "current"),
    (1920, 1080, 59.94, ""),
    (1280, 720, 60.0, ""),
    (1024, 768, 60.004, ""),
    (640, 480, 59.94, ""),
]
FULL_HD_MONITOR = [(1920, 1080, 60.0, "preferred, current"), (1280, 720, 60.0, ""), (1024, 768, 60.004, "")]
XGA_PROJECTOR = [(1024, 768, 60.004, "preferred, current"), (800, 600, 60.317, ""), (640, 480, 59.94, "")]


def outputs_of(modes, name="HDMI-A-2"):
    return script.parse_wlr_randr(wlr_text(name, modes))


# ---- the setting's values ----------------------------------------------------

@pytest.mark.parametrize("value", ["4k", "1080p", "720p", "1024x768", "1280x1024", "640x480"])
def test_valid_values(value):
    assert pairing.valid_screen_resolution(value)


@pytest.mark.parametrize("value", ["", "8k", "1080", "1080P", "1024x", "x768", "1024x768; reboot", "../etc", "10x10"])
def test_invalid_values(value):
    assert not pairing.valid_screen_resolution(value)


def test_unset_or_garbage_means_1080p(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "SCREEN_RESOLUTION_FILE", tmp_path / "screen-resolution")
    assert pairing.read_screen_resolution() == "1080p"
    (tmp_path / "screen-resolution").write_text("potato")
    assert pairing.read_screen_resolution() == "1080p"
    pairing.write_screen_resolution("1024x768")
    assert pairing.read_screen_resolution() == "1024x768"


def test_endpoint_rejects_unknown_value():
    with pytest.raises(HTTPException) as err:
        asyncio.run(main.screen_resolution_set(main.ScreenResolutionRequest(value="8k")))
    assert err.value.status_code == 422


def test_endpoint_saves_applies_and_returns_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "SCREEN_RESOLUTION_FILE", tmp_path / "screen-resolution")
    report = {"state": "fallback", "target": "3840x2160", "current": {"width": 1920, "height": 1080, "refresh": 60.0}}

    async def fake_apply():
        return report

    monkeypatch.setattr(system_control, "apply_screen_resolution", fake_apply)
    data = asyncio.run(main.screen_resolution_set(main.ScreenResolutionRequest(value=" 4K ")))
    assert data == {"ok": True, "screen_resolution": "4k", "display": report}
    assert pairing.read_screen_resolution() == "4k"


def test_status_without_a_report_from_an_old_script(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "SCREEN_RESOLUTION_FILE", tmp_path / "screen-resolution")

    async def fake_report():
        return None

    monkeypatch.setattr(system_control, "screen_resolution_report", fake_report)
    assert asyncio.run(main.screen_resolution_status()) == {"screen_resolution": "1080p", "display": None}


# ---- the script: parsing -----------------------------------------------------

@needs_script
def test_parses_wlr_randr_text():
    [out] = outputs_of(UHD_TV)
    assert out["name"] == "HDMI-A-2" and out["enabled"]
    assert len(out["modes"]) == 7
    assert [m["preferred"] for m in out["modes"]][:2] == [True, False]
    assert script.current_mode(out["modes"])["height"] == 1080
    assert script.sizes(out["modes"])[0] == (3840, 2160)


@needs_script
def test_parses_two_outputs():
    outs = script.parse_wlr_randr(wlr_text("HDMI-A-1", FULL_HD_MONITOR) + wlr_text("HDMI-A-2", UHD_TV))
    assert [o["name"] for o in outs] == ["HDMI-A-1", "HDMI-A-2"]
    assert len(outs[0]["modes"]) == 3 and len(outs[1]["modes"]) == 7


# ---- the script: choosing a mode ---------------------------------------------

def choose(modes, requested):
    target = script.parse_target(requested)
    m = script.choose_mode(outputs_of(modes)[0]["modes"], *target)
    return (m["width"], m["height"], m["refresh"])


@needs_script
@pytest.mark.parametrize("requested,expected", [
    ("4k", (3840, 2160, 30.0)),          # 4K keeps its 30 Hz preference
    ("1080p", (1920, 1080, 60.0)),       # 60.0 over 59.94
    ("720p", (1280, 720, 60.0)),
    ("1024x768", (1024, 768, 60.004)),   # a VESA mode, straight off the display's list
    ("640x480", (640, 480, 59.94)),
])
def test_listed_sizes_are_used_as_asked(requested, expected):
    assert choose(UHD_TV, requested) == expected


@needs_script
def test_unsupported_4k_falls_back_to_the_best_smaller_size():
    assert choose(FULL_HD_MONITOR, "4k")[:2] == (1920, 1080)


@needs_script
def test_unsupported_1080p_falls_back_to_the_best_smaller_size():
    assert choose(XGA_PROJECTOR, "1080p")[:2] == (1024, 768)
    assert choose(XGA_PROJECTOR, "4k")[:2] == (1024, 768)


@needs_script
def test_asking_for_less_than_the_display_has_gets_the_smallest_it_has():
    only_big = [(3840, 2160, 60.0, "preferred, current"), (1920, 1080, 60.0, "")]
    assert choose(only_big, "720p")[:2] == (1920, 1080)
    assert choose([(1920, 1080, 60.0, "preferred")], "1024x768")[:2] == (1920, 1080)


@needs_script
def test_fallback_prefers_the_wanted_aspect_ratio():
    # This TV's real list: 1600x900 isn't there, and 1280x1024 (5:4) is the biggest that fits.
    tv = [(3840, 2160, 30.0, "preferred"), (1920, 1080, 60.0, ""), (1680, 1050, 59.88, ""), (1440, 900, 59.9, ""),
          (1280, 1024, 60.02, ""), (1280, 720, 60.0, ""), (1024, 768, 60.0, "")]
    assert choose(tv, "1600x900")[:2] == (1280, 720)
    # ...but with no 16:9 size that small, any shape beats nothing.
    assert choose([(1024, 768, 60.0, ""), (800, 600, 60.0, "")], "1280x720")[:2] == (1024, 768)


@needs_script
def test_no_modes_chooses_nothing():
    assert script.choose_mode([], 1920, 1080, 60.0) is None


@needs_script
def test_script_reads_the_saved_value(tmp_path):
    f = tmp_path / "res"
    assert script.read_requested(f) == "1080p"           # missing
    f.write_text("potato\n")
    assert script.read_requested(f) == "1080p"           # garbage
    f.write_text("720P\n")
    assert script.read_requested(f) == "720p"
    f.write_text("1024x768")
    assert script.read_requested(f) == "1024x768"


# ---- the script: the report --------------------------------------------------

@needs_script
def test_report_ok():
    r = script.build_report("1080p", outputs_of(UHD_TV))
    assert r["state"] == "ok" and r["current"] == {"width": 1920, "height": 1080, "refresh": 60.0}
    assert r["target"] == "1920x1080" and r["output"] == "HDMI-A-2"
    assert r["modes"][:3] == ["3840x2160", "1920x1080", "1280x720"]


@needs_script
def test_report_failed_when_the_display_lists_it_but_it_is_not_current():
    # The first-boot case: saved "1080p" (or "4k" on a 1080p set), compositor on its own pick.
    assert script.build_report("4k", outputs_of(UHD_TV))["state"] == "failed"


@needs_script
def test_report_fallback_when_the_display_cannot_do_it():
    r = script.build_report("4k", outputs_of(FULL_HD_MONITOR))
    assert r["state"] == "fallback" and r["current"]["width"] == 1920 and r["target"] == "3840x2160"


# ---- the script: two displays mirror one size ---------------------------------

HP_32 = [(1920, 1080, 60.0, "preferred, current"), (1680, 1050, 59.88, ""), (1600, 900, 60.0, ""),
         (1280, 1024, 60.02, ""), (1280, 720, 60.0, ""), (1024, 768, 60.004, ""), (800, 600, 60.317, "")]
TV_55 = [(3840, 2160, 30.0, "preferred"), (1920, 1080, 60.0, "current"), (1680, 1050, 59.88, ""),
         (1280, 1024, 60.02, ""), (1280, 720, 60.0, ""), (1024, 768, 75.03, ""), (640, 480, 59.94, "")]


def two(a=TV_55, b=HP_32):
    return script.parse_wlr_randr(wlr_text("HDMI-A-2", a, "Big TV") + wlr_text("HDMI-A-1", b, "HP 32 Display"))


def plan(outs, requested):
    return script.plan_sizes(outs, *script.parse_target(requested)[:2])


@needs_script
def test_4k_on_a_4k_tv_plus_a_1080p_monitor_is_1080p_on_both():
    # The bug this exists for: TV at 4K, monitor at 1080p, both at 0,0 -> the monitor shows a quarter.
    assert plan(two(), "4k") == {"HDMI-A-2": (1920, 1080), "HDMI-A-1": (1920, 1080)}


@needs_script
def test_a_size_both_list_is_used_by_both():
    assert plan(two(), "1080p") == {"HDMI-A-2": (1920, 1080), "HDMI-A-1": (1920, 1080)}
    assert plan(two(), "720p") == {"HDMI-A-2": (1280, 720), "HDMI-A-1": (1280, 720)}
    assert plan(two(), "1024x768") == {"HDMI-A-2": (1024, 768), "HDMI-A-1": (1024, 768)}


@needs_script
def test_a_size_only_one_lists_falls_back_to_the_closest_both_have():
    # Only the HP lists 1600x900: both go to the best common 16:9-ish size, not their own picks.
    p = plan(two(), "1600x900")
    assert p["HDMI-A-2"] == p["HDMI-A-1"] == (1280, 720)


@needs_script
def test_outputs_with_nothing_in_common_each_get_their_own_closest():
    odd = [(1366, 768, 60.0, "preferred, current")]
    p = plan(two(XGA_PROJECTOR, odd), "1080p")
    assert p == {"HDMI-A-2": (1024, 768), "HDMI-A-1": (1366, 768)}


@needs_script
def test_one_display_is_unchanged_by_all_this():
    assert plan(outputs_of(UHD_TV), "4k") == {"HDMI-A-2": (3840, 2160)}


@needs_script
def test_the_modes_to_offer_are_the_common_ones():
    r = script.build_report("1080p", two())
    assert "3840x2160" not in r["modes"] and r["modes"][0] == "1920x1080"
    assert [o["name"] for o in r["outputs"]] == ["HDMI-A-2", "HDMI-A-1"]
    assert r["outputs"][0]["modes"][0] == "3840x2160"   # each output's own list is still there


@needs_script
def test_two_outputs_on_the_same_size_report_ok_and_mirrored():
    r = script.build_report("1080p", two())
    assert r["state"] == "ok" and r["mirrored"] and r["limited_by"] == []


@needs_script
def test_fallback_names_the_display_that_cannot_do_it():
    r = script.build_report("4k", two())          # both are on 1080p, 4K isn't common
    assert r["state"] == "fallback" and r["mirrored"]
    assert r["limited_by"] == ["HP 32 Display"]


@needs_script
def test_a_broken_mirror_is_reported():
    hp_1080 = HP_32
    tv_4k = [(3840, 2160, 30.0, "preferred, current"), (1920, 1080, 60.0, "")]
    r = script.build_report("1080p", two(tv_4k, hp_1080))
    assert not r["mirrored"] and r["state"] == "failed"


@needs_script
def test_label_drops_the_trailing_output_name():
    [out] = script.parse_wlr_randr('HDMI-A-1 "HP Inc. HP 32 Display CNC82320Z3 (HDMI-A-1)"\n  Enabled: yes\n  Modes:\n')
    assert out["label"] == "HP Inc. HP 32 Display CNC82320Z3"


@needs_script
def test_report_unknown_without_outputs():
    r = script.build_report("1080p", [])
    assert r["state"] == "unknown" and r["current"] is None and r["modes"] == []
    json.dumps(r)
