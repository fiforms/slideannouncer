#!/usr/bin/env python3
"""Watches UDP port 7002 for an incoming SRT stream, validates it against
the configured passphrase, and plays it as a fullscreen Wayland client
inside the kiosk's already-running labwc compositor.

Only active when BOTH the device's own Settings > SRT Sink toggle AND the
admin dashboard's force-disable switch allow it (see
local-app/backend/srt_sink.py's effective_enabled(), which this script
duplicates rather than imports — this is a standalone script installed to
/usr/local/sbin, run outside the backend's venv, so it reads
/data/status/srt-sink.json directly instead). Re-read every poll cycle, so
flipping either one takes effect within one poll interval, no restart of
this unit needed.

Nothing is normally bound to SRT_PORT, so "traffic" can only be observed
by holding a socket there ourselves — there's no listener for `ss`/
`netstat` to show. The poll loop below does exactly that: a plain
non-blocking UDP socket, woken by select() the same way
power-button-monitor.py waits on evdev fds. The very first datagram is
just used as a "something showed up" signal, then the socket is closed
immediately so the port is free for mpv's own SRT listener to bind — a
real SRT sender retries its handshake induction packet on its own cadence
(commonly a few times a second), so losing that first datagram to us
rather than mpv is expected to be harmless in practice.

The one-shot validation probe (mpv, --vo=null --ao=null, output-less) is
what actually decides whether this was a real, correctly-passphrased SRT
caller — SRT's own HSv5 crypto handshake does the passphrase check, not
any comparison here, so a wrong passphrase or a bogus UDP packet fails
fast without ever touching the kiosk.

Playback used to run mpv directly against DRM/KMS with the kiosk (labwc +
Chromium + PipeWire) stopped first via display-power.py's `takeover`.
That worked but had two real problems, both confirmed on hardware: mpv's
DRM vo has no hardware scaler for a v4l2m2m-copy decode buffer, so any
mismatch between the stream's resolution and the configured --drm-mode
fell back to a CPU software blit every frame ("VO: Direct Rendering
Manager (software scaling)" in the journal) — which also meant the
resolution had to be hardcoded to match whatever the stream happened to
send; and audio went out via a bare --ao=alsa, bypassing the PipeWire
graph slide-announcer-apply-audio-output configures for the kiosk, which
is the likely cause of "no sound" reports (silently opening the wrong
ALSA device rather than erroring).

Now: the kiosk is left running (only woken if it was asleep — never
stopped), and mpv connects to labwc's existing Wayland socket as a
fullscreen client (--gpu-context=wayland), the same way Chromium already
does for its own 4K scaling, and plays audio via --ao=pipewire through
the same graph the kiosk already uses. No --drm-mode/resolution hardcode
needed — the compositor scales whatever resolution the stream sends.
Not yet hardware-tested for correct fullscreen stacking above Chromium's
own fullscreen kiosk surface — see rc.xml's own "not yet hardware-tested"
note for the general pattern this repo uses for that caveat.

See system/scripts/display-power.py's own docstring for the sleep/wake
state-transition reasoning `wake`/`sleep` pair with.
"""
import glob
import json
import pwd
import select
import socket
import subprocess
import time
import os
import platform
from pathlib import Path
from urllib.parse import quote

SRT_SINK_CONFIG = Path("/data/status/srt-sink.json")
# Written by this script only — read by local-app/backend/srt_sink.py's
# is_playing() (GET /api/local/srt-sink/playing), which
# frontend/src/views/Slideshow.vue polls on a short interval to pause its
# own crossfade timer and any slide video for the duration of external
# playback. Deliberately a separate file from SRT_SINK_CONFIG: that one
# is secret (0o640) and owned by the backend's enable/passphrase
# read-modify-write cycle, and this one gets rewritten by this script on
# every single playback — mixing the two would risk a lost update
# clobbering the passphrase.
SRT_SINK_PLAYING = Path("/data/status/srt-sink-playing.json")
DISPLAY_POWER_CLI = "/usr/local/sbin/slide-announcer-display-power"
MPV_BIN = "mpv"
KIOSK_USER = "slideannouncer"

SRT_PORT = 7002
POLL_INTERVAL_SECONDS = 2.0
PROBE_TIMEOUT_SECONDS = 5.0
# How long to wait for labwc's Wayland socket to appear after waking the
# kiosk from sleep — startup (pipewire/wireplumber + labwc + Chromium) is
# not instant.
WAYLAND_WAIT_TIMEOUT_SECONDS = 8.0
# Pause between mpv exiting and restoring the sleep state — gives any
# lingering PipeWire teardown from mpv's --ao=pipewire session a moment
# to finish before deciding whether to put the kiosk back to sleep.
CLEANUP_DELAY_SECONDS = 1.0

def get_pi_model():
    """
    Detects the Raspberry Pi model by reading /proc/device-tree/model.
    Returns: 'pi5', 'pi4', 'pi_other', or 'generic_linux'
    """
    model_path = "/proc/device-tree/model"
    if os.path.exists(model_path):
        try:
            with open(model_path, "r") as f:
                model_str = f.read().strip().lower()
                
            if "raspberry pi 5" in model_str:
                return "pi5"
            elif "raspberry pi 4" in model_str or "400" in model_str:
                return "pi4"
            elif "raspberry pi" in model_str:
                return "pi_other"  # Pi 3, Zero 2W, etc.
        except Exception:
            pass
            
    return "generic_linux"

def get_hwdec_flags():
    """
    Returns the optimal mpv hardware decoding arguments based on detected hardware.
    """
    model = get_pi_model()

    if model == "pi4" or model == "pi_other":
        # Pi 4 / 3 have a physical H.264 block using V4L2 M2M. Tried
        # plain v4l2m2m (no -copy) for a zero-copy dmabuf handoff to
        # --gpu-context=wayland, but confirmed on hardware it leaks a
        # duplicated dmabuf fd per frame in this Mesa/v3d driver stack —
        # a ~50s test clip ended in "Failed to duplicate dmabuf fd: Too
        # many open files" and visibly corrupted/frozen frames once the
        # process's fd table filled up. -copy avoids that whole path (a
        # real CPU-side copy per frame instead of a dmabuf export) at the
        # cost of the CPU savings zero-copy would have given — stability
        # over that savings, since any external feed running more than a
        # minute would otherwise eventually hit this.
        return [
            "--hwdec=v4l2m2m-copy",
            "--hwdec-codecs=h264",
        ]
    elif model == "pi5":
        # Pi 5 dropped the dedicated H.264 V4L2 block. 
        # Its fast CPU/GPU handles high-bitrate video via drm-copy / auto-safe zero-copy
        return [
            "--hwdec=drm-copy",
            "--hwdec-codecs=all",
        ]
    else:
        # Generic Linux / Desktop fallback
        return [
            "--hwdec=auto-safe",
            "--hwdec-codecs=all",
        ]

def read_config():
    if not SRT_SINK_CONFIG.exists():
        return {}
    try:
        return json.loads(SRT_SINK_CONFIG.read_text())
    except json.JSONDecodeError:
        return {}


def effective_enabled(config):
    """Mirrors local-app/backend/srt_sink.py's effective_enabled() —
    both the local toggle and the server's force-disable switch must
    allow it, and a passphrase must actually have been generated (it's
    only ever created on first enable, from the Settings UI)."""
    local_enabled = bool(config.get("local_enabled", False))
    server_allows = config.get("server_allows", True) is not False
    passphrase = config.get("passphrase", "")
    return local_enabled and server_allows and bool(passphrase)


def srt_url(passphrase):
    return f"srt://0.0.0.0:{SRT_PORT}?mode=listener&passphrase={quote(passphrase)}&latency=120000"


def open_poll_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", SRT_PORT))
    sock.setblocking(False)
    return sock


def validate_stream(passphrase):
    """One-shot probe: does a caller show up and pass the SRT passphrase
    handshake? --vo=null --ao=null means this never touches the display or
    audio, so a bogus/mis-passphrased caller leaves the kiosk completely
    undisturbed. --frames=1 bounds it to a single decoded frame either way.
    """
    start = time.monotonic()
    proc = subprocess.Popen(
        [
            MPV_BIN, "--no-config", "--vo=null", "--ao=null",
            "--frames=1", "--really-quiet", srt_url(passphrase),
        ],
    )
    try:
        ok = proc.wait(timeout=PROBE_TIMEOUT_SECONDS) == 0
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        ok = False
    print(f"[srt-sink] validate_stream took {time.monotonic() - start:.1f}s ok={ok}", flush=True)
    return ok


def set_playing(active):
    SRT_SINK_PLAYING.parent.mkdir(parents=True, exist_ok=True)
    SRT_SINK_PLAYING.write_text(json.dumps({"active": active}))
    SRT_SINK_PLAYING.chmod(0o644)


def kiosk_runtime_dir():
    return f"/run/user/{pwd.getpwnam(KIOSK_USER).pw_uid}"


def find_wayland_display(runtime_dir):
    """labwc's Wayland socket, e.g. /run/user/<uid>/wayland-1 — glob
    rather than hardcode the number since it's assigned by libwayland-server
    (normally 0 or 1 depending on what else claims a display first) and
    isn't something this codebase controls. Skips the accompanying
    `.lock` file. Returns just the bare `wayland-N` name mpv/libwayland
    expect in $WAYLAND_DISPLAY, or None if the compositor isn't up yet."""
    for candidate in sorted(glob.glob(os.path.join(runtime_dir, "wayland-*"))):
        if not candidate.endswith(".lock"):
            return os.path.basename(candidate)
    return None


def wait_for_wayland_display(runtime_dir, timeout):
    deadline = time.monotonic() + timeout
    while True:
        display = find_wayland_display(runtime_dir)
        if display is not None:
            return display
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)


def handle_candidate_stream(passphrase):
    if not validate_stream(passphrase):
        return

    was_sleeping = subprocess.run(
        [DISPLAY_POWER_CLI, "status"], capture_output=True, text=True, check=False
    ).stdout.strip() == "sleeping"

    if was_sleeping:
        wake_start = time.monotonic()
        subprocess.run([DISPLAY_POWER_CLI, "wake"], check=False)
        print(f"[srt-sink] wake took {time.monotonic() - wake_start:.1f}s", flush=True)

    runtime_dir = kiosk_runtime_dir()
    wait_start = time.monotonic()
    wayland_display = wait_for_wayland_display(runtime_dir, WAYLAND_WAIT_TIMEOUT_SECONDS)
    print(f"[srt-sink] wayland socket wait took {time.monotonic() - wait_start:.1f}s "
          f"display={wayland_display}", flush=True)
    if wayland_display is None:
        print("[srt-sink] no labwc Wayland socket found, aborting playback", flush=True)
        if was_sleeping:
            subprocess.run([DISPLAY_POWER_CLI, "sleep"], check=False)
        return

    hwdec_flags = get_hwdec_flags()
    print(f"[srt-sink] pi model={get_pi_model()} hwdec={hwdec_flags}", flush=True)

    mpv_env = dict(os.environ)
    mpv_env["XDG_RUNTIME_DIR"] = runtime_dir
    mpv_env["WAYLAND_DISPLAY"] = wayland_display

    mpv_cmd = [
        MPV_BIN,
        "--no-config",
        "--fs",
        # Play as a Wayland client inside labwc's existing compositor
        # (same one Chromium's kiosk surface is already in), instead of
        # taking DRM/KMS master directly. The compositor scales whatever
        # resolution the stream sends to the TV's actual mode — the same
        # path that already scales Chromium's rendering to 4K correctly
        # — so no --drm-mode/resolution hardcode is needed here at all.
        #
        # -confirmed on hardware that gpu-next's
        # output swapchain leaks a file descriptor per presented frame on
        # this Mesa/v3d driver stack — a multi-minute test climbed fd
        # count steadily (watched via /proc/<pid>/fd) and eventually hit
        # "MESA: error: Export failed" / growing A-V drift as the leak
        # exhausted the process's fd table, the same failure class as the
        # v4l2m2m (non-copy) hwdec leak below, but in the *output* path
        # this time, independent of hwdec choice. gpu-next is mpv's newer
        # libplacebo-based renderer.
        "--vo=gpu-next",
        "--gpu-context=wayland",
        # PipeWire, not bare ALSA — routes through the same audio graph
        # slide-announcer-apply-audio-output already configured for the
        # kiosk, rather than opening whatever ALSA considers its default
        # device (the likely cause of previous no-sound reports).
        "--ao=pipewire",
        "--framedrop=vo",
        "--demuxer-lavf-o=probesize=32000,analyzeduration=0",
        # Execution Controls
        "--loop=no",
        "--keep-open=no",
        "--idle=no",
        # Left verbose (not --really-quiet) so hwdec negotiation and
        # dropped-frame/decoder stats land in the journal for
        # troubleshooting — this is the one-shot playback run, not the
        # frequent validation probe, so the extra journal volume is fine.
        "--msg-level=all=status,vd=v,cplayer=v",
    ]

    # Inject hardware-specific decoder flags
    mpv_cmd.extend(hwdec_flags)

    # Add the target stream URL
    mpv_cmd.append(srt_url(passphrase))

    print(f"[srt-sink] launching: {' '.join(mpv_cmd)}", flush=True)
    start = time.monotonic()
    set_playing(True)
    try:
        result = subprocess.run(mpv_cmd, env=mpv_env, check=False)
    finally:
        set_playing(False)
    elapsed = time.monotonic() - start
    print(f"[srt-sink] mpv exited code={result.returncode} after {elapsed:.1f}s", flush=True)

    time.sleep(CLEANUP_DELAY_SECONDS)
    # The kiosk was never stopped (only woken, if it was asleep) — its
    # Chromium surface has been sitting underneath mpv's fullscreen
    # surface the whole time, so there's nothing to "wake" back to.
    if was_sleeping:
        subprocess.run([DISPLAY_POWER_CLI, "sleep"], check=False)


def main():
    # In case a previous run crashed mid-playback and left this stuck
    # true — this daemon is Restart=always (see the .service file), so a
    # crash there would otherwise leave the kiosk permanently paused for
    # no visible reason.
    set_playing(False)
    sock = None
    while True:
        config = read_config()

        if not effective_enabled(config):
            if sock is not None:
                sock.close()
                sock = None
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if sock is None:
            sock = open_poll_socket()

        readable, _, _ = select.select([sock], [], [], POLL_INTERVAL_SECONDS)
        if not readable:
            continue

        try:
            sock.recvfrom(65536)
        except OSError:
            pass
        sock.close()
        sock = None  # port must be free before mpv binds its own SRT listener

        handle_candidate_stream(config["passphrase"])


if __name__ == "__main__":
    main()
