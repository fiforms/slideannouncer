#!/usr/bin/python3 -I
"""Sets the kiosk's HDMI output mode via wlr-randr, and reports what it ended up
with. Installed as /usr/local/sbin/slide-announcer-apply-screen-resolution.

    apply-screen-resolution            set the mode, then print the JSON report
    apply-screen-resolution --report   change nothing, just print the JSON report

The wanted resolution is /data/status/screen-resolution (the Settings value):
"4k", "1080p", "720p", or any "WIDTHxHEIGHT" (so every VESA mode the display
lists works, e.g. "1024x768"). Absent/unreadable means 1080p.

The wanted size is a preference, not a promise, and with two displays it is a
mirror: every connected output is set to ONE size, so the picture fills both
(two outputs at different sizes at the same origin would show the bigger one's
top-left corner on the smaller). That size is the wanted one when every output
lists it. Otherwise it is the largest size ALL of them list that is no bigger,
preferring the wanted aspect ratio (the smallest common size if all are bigger),
so asking for 4K with a 4K TV and a 1080p monitor gives 1080p on both. Only if
the outputs share no size at all does each get its own closest one (not a true
mirror; the report says so). A display that can't do 4K comes up at 1080p rather
than staying on whatever the compositor guessed.

Report (stdout, one JSON object):
    requested   the saved setting, e.g. "4k"
    target      the size that setting means, "3840x2160"
    output      the first output's name, e.g. "HDMI-A-2"
    current     the first output's {"width", "height", "refresh"} live right now, or null
    modes       the sizes EVERY output lists, largest first, e.g. ["1920x1080", ...]
                (just that output's own with one display: what Settings can offer)
    outputs     [{"name", "label", "current", "modes"}] for each connected output
    mirrored    every output is on the same size right now
    limited_by  labels of the outputs that don't list the target (empty unless fallback)
    state       "ok"       every output is on the target size;
                "fallback" some output doesn't list the target, so they are on the closest;
                "failed"   every output lists the target but they aren't all on it;
                "unknown"  there is no compositor/output to ask.

Never raises on a bad mode or a missing compositor: a failed switch leaves the
current display alone rather than blanking it. kiosk-start.sh calls this with
`|| true`, and the backend does not look at the exit code.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SCREEN_RESOLUTION_FILE = Path("/data/status/screen-resolution")
DEFAULT = "1080p"

# name -> (width, height, preferred refresh Hz). 4K stays at 30 Hz as before: a
# Pi 4's two HDMI ports share the pixel clock budget, and 30 Hz is plenty for slides.
PRESETS = {
    "4k": (3840, 2160, 30.0),
    "1080p": (1920, 1080, 60.0),
    "720p": (1280, 720, 60.0),
}
SIZE_RE = re.compile(r"^(\d{3,5})x(\d{3,5})$")

OUTPUT_RE = re.compile(r'^(\S+) "(.*)"\s*$')
MODE_RE = re.compile(r"^\s+(\d+)x(\d+) px, ([\d.]+) Hz(?: \(([^)]*)\))?")
ENABLED_RE = re.compile(r"^\s+Enabled: (\w+)")


def parse_target(value):
    """"4k" / "1080p" / "720p" / "1024x768" -> (w, h, preferred Hz), or None."""
    value = (value or "").strip().lower()
    if value in PRESETS:
        return PRESETS[value]
    m = SIZE_RE.match(value)
    if m:
        return int(m.group(1)), int(m.group(2)), 60.0
    return None


def read_requested(path=None):
    path = path or SCREEN_RESOLUTION_FILE
    try:
        value = path.read_text().strip().lower()
    except OSError:
        return DEFAULT
    return value if parse_target(value) else DEFAULT


def parse_wlr_randr(text):
    """`wlr-randr` text -> [{"name", "enabled", "modes": [{"width", "height",
    "refresh", "preferred", "current"}]}]. wlr-randr only lists connected outputs."""
    outputs = []
    for line in text.splitlines():
        m = OUTPUT_RE.match(line)
        if m:
            label = re.sub(r"\s*\(" + re.escape(m.group(1)) + r"\)$", "", m.group(2))
            outputs.append({"name": m.group(1), "label": label, "enabled": True, "modes": []})
            continue
        if not outputs:
            continue
        m = ENABLED_RE.match(line)
        if m:
            outputs[-1]["enabled"] = m.group(1).lower() == "yes"
            continue
        m = MODE_RE.match(line)
        if m:
            flags = (m.group(4) or "").replace(" ", "").split(",")
            outputs[-1]["modes"].append({
                "width": int(m.group(1)),
                "height": int(m.group(2)),
                "refresh": float(m.group(3)),
                "preferred": "preferred" in flags,
                "current": "current" in flags,
            })
    return outputs


def sizes(modes):
    """Distinct (w, h) the output lists, largest first."""
    return sorted({(m["width"], m["height"]) for m in modes}, key=lambda s: (-s[0] * s[1], -s[0]))


def same_shape(size, target_w, target_h):
    """Within 3% of the target's aspect ratio (1280x1024 is not a 16:9 mode)."""
    return abs(size[0] / size[1] - target_w / target_h) <= 0.03 * target_w / target_h


def choose_size(available, target_w, target_h):
    """The target if listed. Else the largest listed size no bigger than it (the
    smallest listed size, if every one is bigger), preferring sizes with the
    target's aspect ratio so a 16:9 TV asked for 1600x900 gets 1280x720, not a
    stretched 1280x1024. None if nothing is listed."""
    if not available:
        return None
    if (target_w, target_h) in available:
        return (target_w, target_h)
    limit = target_w * target_h
    smaller = [s for s in available if s[0] * s[1] <= limit]
    if smaller:
        pool = [s for s in smaller if same_shape(s, target_w, target_h)] or smaller
        return max(pool, key=lambda s: (s[0] * s[1], s[0]))
    pool = [s for s in available if same_shape(s, target_w, target_h)] or available
    return min(pool, key=lambda s: (s[0] * s[1], s[0]))


def choose_mode(modes, target_w, target_h, want_hz):
    """The mode to set, or None. Within the chosen size, the refresh closest to
    `want_hz` (the display's own preferred mode, then list order, break ties)."""
    size = choose_size(sizes(modes), target_w, target_h)
    if size is None:
        return None
    same = [m for m in modes if (m["width"], m["height"]) == size]
    return min(same, key=lambda m: (abs(m["refresh"] - want_hz), not m["preferred"]))


def current_mode(modes):
    for m in modes:
        if m["current"]:
            return m
    return None


def live_outputs(outputs):
    """The outputs that are on; if somehow none is, all of them."""
    return [o for o in outputs if o["enabled"]] or outputs


def common_sizes(outputs):
    """Sizes every given output lists, largest first."""
    sets = [set(sizes(o["modes"])) for o in outputs]
    common = set.intersection(*sets) if sets else set()
    return sorted(common, key=lambda s: (-s[0] * s[1], -s[0]))


def plan_sizes(outputs, target_w, target_h):
    """{output name: size to set}. One shared size when the outputs have any in
    common (see the module docstring); else each its own closest."""
    outputs = live_outputs(outputs)
    shared = choose_size(common_sizes(outputs), target_w, target_h)
    if shared:
        return {o["name"]: shared for o in outputs}
    return {o["name"]: choose_size(sizes(o["modes"]), target_w, target_h) for o in outputs}


def build_report(requested, outputs):
    target = parse_target(requested) or PRESETS[DEFAULT]
    target_size = (target[0], target[1])
    report = {
        "requested": requested,
        "target": f"{target[0]}x{target[1]}",
        "output": None,
        "current": None,
        "modes": [],
        "outputs": [],
        "mirrored": True,
        "limited_by": [],
        "state": "unknown",
    }
    if not outputs:
        return report
    live = live_outputs(outputs)
    currents = []
    for o in live:
        cur = current_mode(o["modes"]) if o["enabled"] else None
        currents.append(cur)
        report["outputs"].append({
            "name": o["name"],
            "label": o.get("label", ""),
            "current": {"width": cur["width"], "height": cur["height"], "refresh": round(cur["refresh"], 2)} if cur else None,
            "modes": [f"{w}x{h}" for w, h in sizes(o["modes"])],
        })
    report["output"] = report["outputs"][0]["name"]
    report["current"] = report["outputs"][0]["current"]
    report["modes"] = [f"{w}x{h}" for w, h in (common_sizes(live) or sizes(live[0]["modes"]))]
    if any(c is None for c in currents):
        return report
    current_sizes = {(c["width"], c["height"]) for c in currents}
    report["mirrored"] = len(current_sizes) == 1
    if current_sizes == {target_size}:
        report["state"] = "ok"
    elif all(target_size in sizes(o["modes"]) for o in live):
        report["state"] = "failed"
    else:
        report["state"] = "fallback"
        report["limited_by"] = [o.get("label") or o["name"] for o in live if target_size not in sizes(o["modes"])]
    return report


def wlr_env():
    """wlr-randr talks to the compositor over the Wayland socket, which isn't
    inherited automatically by a caller outside labwc's process tree (the
    backend runs in its own service, with no session). kiosk-start.sh exports
    XDG_RUNTIME_DIR before starting labwc, so that is a no-op for the boot call.
    labwc takes the first free socket name (normally wayland-0), so look for the
    real one rather than guessing a number."""
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env["XDG_RUNTIME_DIR"] = runtime
    if not env.get("WAYLAND_DISPLAY"):
        try:
            socks = sorted(n for n in os.listdir(runtime) if re.fullmatch(r"wayland-\d+", n))
        except OSError:
            socks = []
        env["WAYLAND_DISPLAY"] = socks[0] if socks else "wayland-0"
    return env


def run_wlr_randr(args, env):
    try:
        proc = subprocess.run(["wlr-randr", *args], env=env, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"apply-screen-resolution: wlr-randr {' '.join(args)}: {exc}", file=sys.stderr)
        return None
    if proc.returncode != 0:
        print(f"apply-screen-resolution: wlr-randr {' '.join(args)}: {proc.stderr.strip()}", file=sys.stderr)
        return None
    return proc.stdout


def query(env, attempts=5):
    """Connected outputs, retrying briefly: at boot labwc may not have finished
    initializing its outputs yet (same reasoning as apply-audio-output.py's wpctl retry)."""
    for i in range(attempts):
        out = run_wlr_randr([], env)
        outputs = parse_wlr_randr(out) if out else []
        if outputs:
            return outputs
        if i < attempts - 1:
            time.sleep(1)
    return []


def apply(requested, env):
    target = parse_target(requested)
    outputs = query(env)
    if not outputs:
        print("apply-screen-resolution: wlr-randr reported no outputs", file=sys.stderr)
        return []
    plan = plan_sizes(outputs, target[0], target[1])
    for output in live_outputs(outputs):
        size = plan.get(output["name"])
        if size is None:
            print(f"apply-screen-resolution: {output['name']} lists no modes", file=sys.stderr)
            continue
        mode = choose_mode(output["modes"], size[0], size[1], target[2])
        if size != (target[0], target[1]):
            print(f"apply-screen-resolution: {output['name']} set to {size[0]}x{size[1]} "
                  f"(wanted {target[0]}x{target[1]}; not every connected display lists it)", file=sys.stderr)
        # Every output at the same origin: a mirror, not an extended desktop (DISPLAY_IMPLEMENTATION.md).
        spec = f"{mode['width']}x{mode['height']}@{mode['refresh']:.6f}Hz"
        run_wlr_randr(["--output", output["name"], "--mode", spec, "--pos", "0,0"], env)
    # Re-read, so the report is what the compositor really did, not what we asked for.
    return parse_wlr_randr(run_wlr_randr([], env) or "")


def main(argv):
    env = wlr_env()
    requested = read_requested()
    if "--report" in argv:
        out = run_wlr_randr([], env)
        outputs = parse_wlr_randr(out) if out else []
    else:
        outputs = apply(requested, env)
    report = build_report(requested, outputs)
    print(json.dumps(report))
    return 0 if report["state"] in ("ok", "fallback") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
