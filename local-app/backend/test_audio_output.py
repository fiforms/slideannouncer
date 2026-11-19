"""The audio output choice (HDMI automatic / HDMI 1 / HDMI 2 / headphone jack / all outputs):
which values are valid, and how system/scripts/apply-audio-output.py turns one into a
PipeWire default sink and reports it. The graph fixtures are shaped like a real Pi 4's
`pw-dump` (vc4-hdmi-0 is HDMI-A-1, vc4-hdmi-1 is HDMI-A-2)."""
import asyncio
import importlib.machinery
import importlib.util
import os

import pytest
from fastapi import HTTPException

import main
import pairing
import system_control

ROOT = os.environ.get("PLATFORM_ROOT")
needs_script = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")


def load_script():
    loader = importlib.machinery.SourceFileLoader("apply_audio_output", f"{ROOT}/system/scripts/apply-audio-output.py")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


script = load_script() if ROOT else None

HDMI_DESC = "Built-in Audio Digital Stereo (HDMI)"


def sink(id_, name, desc, card_name=None, card="0"):
    props = {"media.class": "Audio/Sink", "node.name": name, "node.description": desc, "alsa.card": card}
    if card_name:
        props["alsa.card_name"] = card_name
    return {"id": id_, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def hdmi1(id_=71):
    return sink(id_, "alsa_output.platform-fef00700.hdmi.hdmi-stereo", HDMI_DESC, "vc4-hdmi-0", "2")


def hdmi2(id_=72):
    return sink(id_, "alsa_output.platform-fef05700.hdmi.hdmi-stereo", HDMI_DESC, "vc4-hdmi-1", "3")


def jack(id_=70):
    return sink(id_, "alsa_output.platform-fe00b840.mailbox.stereo-fallback", "Built-in Audio Stereo", "bcm2835 Headphones", "0")


def combined(id_=90):
    return sink(id_, "slide_announcer_all_outputs", "All outputs")


def default(name):
    return {"id": 30, "type": "PipeWire:Interface:Metadata", "props": {"metadata.name": "default"},
            "metadata": [{"subject": 0, "key": "default.audio.sink", "type": "Spa:String:JSON", "value": {"name": name}}]}


def graph_of(*objs, default_name=None):
    dump = list(objs) + ([default(default_name)] if default_name else [])
    return script.parse_graph(dump)


BOTH = (jack(), hdmi1(), hdmi2())
HDMI1_NAME = "alsa_output.platform-fef00700.hdmi.hdmi-stereo"
HDMI2_NAME = "alsa_output.platform-fef05700.hdmi.hdmi-stereo"


# ---- the setting's values ----------------------------------------------------

@pytest.mark.parametrize("value", ["hdmi", "hdmi1", "hdmi2", "headphones", "all"])
def test_valid_values(value):
    assert value in pairing.AUDIO_OUTPUTS


def test_unset_or_garbage_means_automatic(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "AUDIO_OUTPUT_FILE", tmp_path / "audio-output")
    assert pairing.read_audio_output() == "hdmi"
    (tmp_path / "audio-output").write_text("speakers")
    assert pairing.read_audio_output() == "hdmi"
    for value in pairing.AUDIO_OUTPUTS:
        pairing.write_audio_output(value)
        assert pairing.read_audio_output() == value


def test_endpoint_rejects_unknown_value():
    with pytest.raises(HTTPException) as err:
        asyncio.run(main.audio_output_set(main.AudioOutputRequest(value="speakers")))
    assert err.value.status_code == 422


def test_endpoint_saves_applies_and_returns_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "AUDIO_OUTPUT_FILE", tmp_path / "audio-output")
    report = {"state": "ok", "effective": "hdmi2"}

    async def fake_apply():
        return report

    monkeypatch.setattr(system_control, "apply_audio_output", fake_apply)
    data = asyncio.run(main.audio_output_set(main.AudioOutputRequest(value=" HDMI2 ")))
    assert data == {"ok": True, "audio_output": "hdmi2", "audio": report}
    assert pairing.read_audio_output() == "hdmi2"


def test_status_without_a_report_from_an_old_script(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "AUDIO_OUTPUT_FILE", tmp_path / "audio-output")

    async def fake_report():
        return None

    monkeypatch.setattr(system_control, "audio_output_report", fake_report)
    assert asyncio.run(main.audio_output_status()) == {"audio_output": "hdmi", "audio": None}


# ---- the script: reading the graph -------------------------------------------

@needs_script
def test_parses_sinks_and_default():
    g = graph_of(*BOTH, default_name=HDMI1_NAME)
    assert [(s["kind"], s["port"]) for s in g["sinks"]] == [("headphones", None), ("hdmi", 1), ("hdmi", 2)]
    assert g["default"] == HDMI1_NAME


@needs_script
def test_sources_and_other_nodes_are_ignored():
    src = sink(5, "alsa_input.usb", "USB Composite Device Mono")
    src["info"]["props"]["media.class"] = "Audio/Source"
    g = graph_of(src, {"id": 6, "type": "PipeWire:Interface:Device", "info": {"props": {}}}, hdmi2())
    assert [s["id"] for s in g["sinks"]] == [72]


@needs_script
def test_a_lone_display_on_port_2_is_hdmi2_but_automatic_still_finds_it():
    g = graph_of(jack(), hdmi2())
    assert script.kind_of(g["sinks"][1], g) == "hdmi2"
    assert script.choose("hdmi", g)[0]["id"] == 72


# ---- the script: choosing ----------------------------------------------------

def pick(requested, *objs):
    sink_, fallback = script.choose(requested, graph_of(*objs))
    return (sink_["id"] if sink_ else None), fallback


@needs_script
@pytest.mark.parametrize("requested,expected", [
    ("hdmi", (71, False)),        # automatic: the first display's output
    ("hdmi1", (71, False)),
    ("hdmi2", (72, False)),
    ("headphones", (70, False)),
])
def test_each_choice_with_two_displays(requested, expected):
    assert pick(requested, *BOTH) == expected


@needs_script
def test_all_uses_the_combined_sink_when_there_are_two_outputs():
    assert pick("all", *BOTH, combined()) == (90, False)
    assert pick("all", jack(), hdmi2(), combined()) == (90, False)     # one display + the jack is two outputs


@needs_script
def test_all_feeds_the_hdmi_outputs_and_the_jack():
    g = graph_of(*BOTH, combined(), sink(5, "alsa_output.usb-xyz.analog-stereo", "USB Audio"))
    assert [s["id"] for s in script.output_sinks(g)] == [71, 72, 70]     # HDMI 1, HDMI 2, jack; not USB or itself


@needs_script
def test_all_with_a_single_output_is_just_that_output():
    assert pick("all", hdmi2(), combined()) == (72, False)
    assert pick("all", jack(), combined()) == (70, False)


@needs_script
def test_all_without_the_combined_sink_falls_back_to_automatic():
    # An OS image without the pipewire config: say so rather than going silent.
    assert pick("all", *BOTH) == (71, True)


@needs_script
def test_a_port_with_no_display_falls_back_to_the_other_instead_of_silence():
    assert pick("hdmi1", jack(), hdmi2()) == (72, True)
    assert pick("hdmi2", jack(), hdmi1()) == (71, True)


@needs_script
def test_headphones_missing_falls_back_to_hdmi():
    assert pick("headphones", hdmi1()) == (71, True)


@needs_script
def test_nothing_to_pick():
    assert pick("hdmi") == (None, False)
    assert pick("hdmi2", jack()) == (None, False)


# ---- the script: the report --------------------------------------------------

LABELS = {"2": "HP 32 Display", "3": "55S435"}


def report_for(requested, *objs, default_name=None):
    return script.build_report(requested, graph_of(*objs, default_name=default_name), label_for=lambda card: LABELS.get(card, ""))


@needs_script
def test_report_names_the_display_on_each_port():
    r = report_for("hdmi", *BOTH, default_name=HDMI1_NAME)
    assert [(p["id"], p["connector"], p["present"], p.get("label")) for p in r["ports"][:2]] == [
        ("hdmi1", "HDMI-A-1", True, "HP 32 Display"), ("hdmi2", "HDMI-A-2", True, "55S435")]
    assert r["ports"][2] == {"id": "headphones", "present": True}
    assert r["effective"] == "hdmi1" and r["state"] == "ok"


@needs_script
def test_report_ok_for_each_choice():
    for requested, name, effective in [("hdmi2", HDMI2_NAME, "hdmi2"), ("headphones", "alsa_output.platform-fe00b840.mailbox.stereo-fallback", "headphones")]:
        r = report_for(requested, *BOTH, default_name=name)
        assert (r["effective"], r["state"]) == (effective, "ok")


@needs_script
def test_report_all():
    r = report_for("all", *BOTH, combined(), default_name="slide_announcer_all_outputs")
    assert (r["effective"], r["state"], r["all_available"]) == ("all", "ok", True)


@needs_script
def test_all_is_not_offered_with_a_single_output_or_without_the_sink():
    assert not report_for("hdmi", hdmi2(), combined(), default_name=HDMI2_NAME)["all_available"]
    assert not report_for("hdmi", *BOTH, default_name=HDMI1_NAME)["all_available"]
    assert report_for("hdmi", jack(), hdmi2(), combined(), default_name=HDMI2_NAME)["all_available"]


@needs_script
def test_report_fallback_when_the_port_has_no_display():
    r = report_for("hdmi1", jack(), hdmi2(), default_name=HDMI2_NAME)
    assert (r["effective"], r["state"]) == ("hdmi2", "fallback")
    assert r["ports"][0]["present"] is False and r["ports"][0]["label"] == ""


@needs_script
def test_report_failed_when_the_default_is_somewhere_else():
    assert report_for("hdmi2", *BOTH, default_name=HDMI1_NAME)["state"] == "failed"


@needs_script
def test_report_unknown_when_pipewire_is_not_answering():
    r = script.build_report("hdmi", None)
    assert r["state"] == "unknown" and r["effective"] is None and r["ports"] == []


@needs_script
def test_saved_values(tmp_path):
    f = tmp_path / "out"
    assert script.read_requested(f) == "hdmi"
    f.write_text("HDMI2\n")
    assert script.read_requested(f) == "hdmi2"
    f.write_text("potato")
    assert script.read_requested(f) == "hdmi"
    v = tmp_path / "vol"
    assert script.read_volume(v) == 100
    v.write_text("55\n")
    assert script.read_volume(v) == 55
    v.write_text("loud")
    assert script.read_volume(v) == 100
    m = tmp_path / "mute"
    assert script.read_muted(m) is False
    m.write_text("true")
    assert script.read_muted(m) is True
