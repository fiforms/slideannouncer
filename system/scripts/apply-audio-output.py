#!/usr/bin/python3 -I
"""Points PipeWire's default sink at the audio output chosen in Settings, re-applies
the saved volume/mute, and reports what is plugged in and where sound goes now.
Installed as /usr/local/sbin/slide-announcer-apply-audio-output.

    apply-audio-output            set the default sink, then print the JSON report
    apply-audio-output --report   change nothing, just print the JSON report

The choice is /data/status/audio-output (the Settings value):
    hdmi        automatic: the first HDMI output that has a display (the default; follows
                a lone TV whichever port it is on)
    hdmi1       HDMI-A-1, the vc4-hdmi-0 audio device
    hdmi2       HDMI-A-2, the vc4-hdmi-1 audio device
    headphones  the 3.5mm jack (whichever Built-in Audio sink is not an HDMI one)
    all         every output at once (each HDMI output and the jack), through the "All
                outputs" combined sink that
                /etc/pipewire/pipewire.conf.d/50-slide-announcer-all-outputs.conf defines.
                (Separate outputs run on independent clocks, so their sound can drift apart
                over a long time; that is why this is opt-in.)
A missing/unreadable file means "hdmi".

PipeWire only creates an HDMI sink while a display is connected to that port, so a
choice that isn't available (hdmi2 with nothing on HDMI 2) falls back to automatic
rather than going silent. The report says so.

Report (stdout, one JSON object):
    requested   the saved choice
    effective   where sound goes right now: "hdmi1" | "hdmi2" | "headphones" | "all" | "other" | null
    state       "ok"       sound is going where the choice says;
                "fallback" the choice isn't available, so it is going to the closest;
                "failed"   it is available but the default sink isn't it;
                "unknown"  PipeWire isn't answering.
    ports       [{"id": "hdmi1", "connector": "HDMI-A-1", "present": bool, "label": monitor name}, ...,
                 {"id": "headphones", "present": bool}]
    all_available   the combined sink exists AND there are two or more outputs to feed

Never raises: a missing/renamed sink leaves whatever output was active alone rather than
taking down the caller (kiosk-start.sh calls this with `|| true`, and the backend does not
look at the exit code).

Why not parse `wpctl status`: this reads `pw-dump` JSON instead, which names each sink's
ALSA card (vc4-hdmi-N -> which HDMI port) and the current default directly, rather than
guessing from the human-readable listing.
"""
import glob
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

AUDIO_OUTPUT_FILE = Path("/data/status/audio-output")
VOLUME_FILE = Path("/data/status/audio-volume")
MUTED_FILE = Path("/data/status/audio-muted")
DEFAULT = "hdmi"
CHOICES = ("hdmi", "hdmi1", "hdmi2", "headphones", "all")
COMBINED_NODE = "slide_announcer_all_outputs"
HDMI_CARD_RE = re.compile(r"^vc4-hdmi-(\d+)$")


def read_requested(path=None):
    try:
        value = (path or AUDIO_OUTPUT_FILE).read_text().strip().lower()
    except OSError:
        return DEFAULT
    return value if value in CHOICES else DEFAULT


def read_volume(path=None):
    """Saved volume percent (default 100): a fresh boot or an output switch would
    otherwise leave the new default sink at PipeWire's own flat 40%."""
    try:
        text = (path or VOLUME_FILE).read_text().strip()
    except OSError:
        return 100
    return int(text) if re.fullmatch(r"[0-9]+", text) else 100


def read_muted(path=None):
    try:
        return (path or MUTED_FILE).read_text().strip() == "true"
    except OSError:
        return False


# ---- the PipeWire graph ------------------------------------------------------

def classify(props):
    """("hdmi", port) | ("headphones", None) | ("combined", None) | ("other", None)
    for a sink's node properties."""
    if props.get("node.name") == COMBINED_NODE:
        return "combined", None
    card = HDMI_CARD_RE.match(props.get("alsa.card_name", ""))
    desc = props.get("node.description", "")
    if card:
        return "hdmi", int(card.group(1)) + 1
    if "hdmi" in desc.lower():
        return "hdmi", None
    if "built-in audio" in desc.lower():
        return "headphones", None
    return "other", None


def parse_graph(dump):
    """pw-dump JSON -> {"sinks": [...], "default": sink name | None}. A sink is
    {"id", "name", "description", "card", "kind", "port"}."""
    sinks, default = [], None
    for obj in dump:
        otype = obj.get("type", "")
        if otype == "PipeWire:Interface:Node":
            props = (obj.get("info") or {}).get("props") or {}
            if props.get("media.class") != "Audio/Sink":
                continue
            kind, port = classify(props)
            sinks.append({
                "id": obj["id"],
                "name": props.get("node.name", ""),
                "description": props.get("node.description", ""),
                "card": props.get("alsa.card"),
                "kind": kind,
                "port": port,
            })
        elif otype == "PipeWire:Interface:Metadata" and (obj.get("props") or {}).get("metadata.name") == "default":
            for entry in obj.get("metadata") or []:
                if entry.get("key") == "default.audio.sink":
                    value = entry.get("value")
                    default = value.get("name") if isinstance(value, dict) else None
    return {"sinks": sinks, "default": default}


def output_sinks(graph):
    """Every sink "all" feeds: the HDMI outputs, then the jack."""
    return hdmi_sinks(graph) + [s for s in graph["sinks"] if s["kind"] == "headphones"]


def hdmi_sinks(graph):
    """HDMI sinks, port order (a sink with no known port last, then by id)."""
    return sorted((s for s in graph["sinks"] if s["kind"] == "hdmi"),
                  key=lambda s: (s["port"] is None, s["port"] or 0, s["id"]))


def kind_of(sink, graph):
    """What Settings calls this sink: hdmi1 | hdmi2 | headphones | all | other."""
    if sink["kind"] == "combined":
        return "all"
    if sink["kind"] == "hdmi":
        order = hdmi_sinks(graph)
        port = sink["port"] or (order.index(sink) + 1)
        return f"hdmi{port}"
    return "headphones" if sink["kind"] == "headphones" else "other"


def choose(requested, graph):
    """(sink | None, fallback). `fallback` is True when the choice itself wasn't
    available and the closest thing was picked instead."""
    hdmis = hdmi_sinks(graph)
    combined = next((s for s in graph["sinks"] if s["kind"] == "combined"), None)
    headphones = next((s for s in graph["sinks"] if s["kind"] == "headphones"), None)
    auto = hdmis[0] if hdmis else None
    if requested == "headphones":
        return (headphones, False) if headphones else (auto, auto is not None)
    if requested == "all":
        outputs = output_sinks(graph)
        if len(outputs) <= 1:
            return (outputs[0] if outputs else None), False   # one output: nothing to duplicate onto
        return (combined, False) if combined else (auto, auto is not None)
    if requested in ("hdmi1", "hdmi2"):
        port = int(requested[-1])
        exact = next((s for s in hdmis if (s["port"] or (hdmis.index(s) + 1)) == port), None)
        return (exact, False) if exact else (auto, auto is not None)
    return auto, False


def monitor_name(card):
    """The display's name from its ELD (what the display told the HDMI audio device
    about itself); "" if none connected."""
    for path in glob.glob(f"/proc/asound/card{card}/eld#*"):
        try:
            for line in Path(path).read_text().splitlines():
                if line.startswith("monitor_name"):
                    return line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
        except OSError:
            continue
    return ""


def build_report(requested, graph, label_for=monitor_name):
    report = {"requested": requested, "effective": None, "state": "unknown", "ports": [], "all_available": False}
    if graph is None:
        return report
    hdmis = hdmi_sinks(graph)
    for port in (1, 2):
        sink = next((s for s in hdmis if (s["port"] or (hdmis.index(s) + 1)) == port), None)
        report["ports"].append({
            "id": f"hdmi{port}",
            "connector": f"HDMI-A-{port}",
            "present": sink is not None,
            "label": label_for(sink["card"]) if sink and sink["card"] is not None else "",
        })
    report["ports"].append({"id": "headphones", "present": any(s["kind"] == "headphones" for s in graph["sinks"])})
    report["all_available"] = len(output_sinks(graph)) >= 2 and any(s["kind"] == "combined" for s in graph["sinks"])
    default = next((s for s in graph["sinks"] if s["name"] == graph["default"]), None)
    if default:
        report["effective"] = kind_of(default, graph)
    wanted, fallback = choose(requested, graph)
    if wanted is None or default is None:
        report["state"] = "failed" if graph["sinks"] else "unknown"
    elif default["id"] != wanted["id"]:
        report["state"] = "failed"
    else:
        report["state"] = "fallback" if fallback else "ok"
    return report


# ---- talking to PipeWire -----------------------------------------------------

def pw_env():
    """PipeWire/WirePlumber run under the XDG_RUNTIME_DIR kiosk-start.sh exports
    before backgrounding them there. The OTHER caller, the backend, runs in
    slide-announcer-backend.service, which has no systemd --user session to inherit
    it from — without this every call below would talk to no PipeWire at all
    (which is why the Settings switch once changed the selection but never re-routed
    sound)."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return env


def run(cmd, env, timeout=3):
    """Each call is capped: a half-initialized PipeWire (e.g. the HDMI audio codec not
    yet registered at early boot) can block on an IPC reply that never comes, which
    would otherwise hang kiosk-start.sh — it calls this inline before `exec labwc`,
    so Chromium would never start."""
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"apply-audio-output: {' '.join(cmd)}: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"apply-audio-output: {' '.join(cmd)}: {proc.stderr.strip()}", file=sys.stderr)
        return None
    return proc.stdout


def read_graph(env):
    out = run(["pw-dump"], env)
    if not out:
        return None
    try:
        return parse_graph(json.loads(out))
    except ValueError:
        return None


def apply(requested, env, attempts=5):
    """Wait (briefly) for the wanted sink to exist — PipeWire may still be coming up,
    and kiosk-start.sh only sleeps 1s after starting it — then make it the default."""
    graph, sink = None, None
    for i in range(attempts):
        graph = read_graph(env)
        if graph:
            sink, fallback = choose(requested, graph)
            # An exact answer, or the best there is after waiting; don't spin forever
            # on a port that simply has no display.
            if sink and not fallback:
                break
        if i < attempts - 1:
            time.sleep(1)
    if not graph or sink is None:
        print(f"apply-audio-output: no sink found for '{requested}'", file=sys.stderr)
        return graph
    if fallback:
        print(f"apply-audio-output: '{requested}' isn't available; using {sink['description']} ({kind_of(sink, graph)})",
              file=sys.stderr)
    run(["wpctl", "set-default", str(sink["id"])], env)
    volume, muted = read_volume(), read_muted()
    if sink["kind"] == "combined":
        # The combined sink's volume is the one the remote controls, and the sinks
        # under it must not scale it down again (they each default to 40%).
        for member in output_sinks(graph):
            run(["wpctl", "set-volume", str(member["id"]), "100%"], env)
            run(["wpctl", "set-mute", str(member["id"]), "0"], env)
    run(["wpctl", "set-volume", str(sink["id"]), f"{volume}%"], env)
    run(["wpctl", "set-mute", str(sink["id"]), "1" if muted else "0"], env)
    return read_graph(env)


def main(argv):
    env = pw_env()
    requested = read_requested()
    graph = read_graph(env) if "--report" in argv else apply(requested, env)
    report = build_report(requested, graph)
    print(json.dumps(report))
    return 0 if report["state"] in ("ok", "fallback") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
