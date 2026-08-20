#!/usr/bin/env python3
"""Watches UDP port 7002 for an incoming SRT stream, validates it against
the configured passphrase, and takes over the display to play it.

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
fast without ever touching the kiosk. Only a validated stream reaches
system/scripts/display-power.py's `takeover` action (stop kiosk / ensure
HDMI on, without touching the sleep-state marker) and real playback.

A Wayland-client approach (mpv running inside the kiosk's own labwc
compositor instead of stopping it, --gpu-context=wayland) was tried and
reverted — see the srt-sink-wayland-experiment git branch for that
history. Two separate, confirmed-on-hardware failures killed it: mpv's
--vo=gpu-next leaked a file descriptor per presented frame in this
Mesa/v3d driver stack (a multi-minute clip climbed steadily via
/proc/<pid>/fd and eventually hit "MESA: error: Export failed"), and
--vo=gpu (older renderer, same Wayland context) instead deadlocked
completely after its first frame (playback position frozen while Cache
grew unbounded — a blocked buffer swap waiting on a compositor event that
never arrived). Both point at sharing labwc with another already-
fullscreen client (Chromium) being fundamentally unreliable on this
driver stack, not just mistuned flags.

Back to direct DRM/KMS here. A GPU-shader attempt at this same layer
(--vo=gpu --gpu-context=drm, no compositor, hoping to avoid legacy
--vo=drm's CPU-side scaling) was also tried and confirmed broken on
hardware a different way again: decode/demux kept up fine (Cache stayed
flat, ~1.1s, never overflowing) but presentation couldn't keep pace — A-V
grew past 14 seconds unbounded, framedrop=vo never kicking in to
compensate since Dropped stayed flat too. That's three different
GPU-accelerated renderers (--vo=gpu-next and --vo=gpu under Wayland, now
--vo=gpu under direct DRM) each failing a different way on this Pi4/
Mesa/v3d driver stack. mpv's *legacy* --vo=drm is the only configuration
that's ever held A-V flush at 0.000 through a whole clip in this
investigation — it costs real CPU (~200-235%, confirmed) doing its
scaling/color-conversion via libswscale on the CPU instead of GPU shaders
("VO: Direct Rendering Manager (software scaling)" in the journal), and
--drm-mode has to be hardcoded to match the stream's own resolution
(confirmed 1920x1080) since it has no hardware scaler to fall back on —
but that known, bounded cost beats three separate GPU-path failures.

Audio's problem was separate from the video backend entirely: it went
out via a bare --ao=alsa, opening whatever ALSA considered its default
device rather than the HDMI sink slide-announcer-apply-audio-output
configures — the likely cause of "no sound" reports. --ao=pipewire fixes
that, but getting PipeWire itself to survive `takeover` stopping the
kiosk took two wrong turns before landing on the real fix, both confirmed
on hardware:
- First tried hand-rolling a second, independent PipeWire instance in
  its own persistent unit (slide-announcer-audio.service). This actively
  conflicted with the OS's own default per-user PipeWire/WirePlumber/
  pipewire-pulse systemd --user units — which auto-start for any real
  login session, including the one slide-announcer-kiosk.service's
  PAMName=login already creates — competing for the same pipewire-pulse
  socket and "org.pulseaudio.Server" D-Bus name. That's a real,
  already-running PipeWire instance this codebase just never knew about
  before; retired the hand-rolled one entirely rather than keep two.
- The OS's own instance normally dies with the kiosk's login session
  when `takeover` stops it, same problem as before just one layer up.
  Fixed with `loginctl enable-linger slideannouncer` (baked into the
  image at build time — see 01-system-files/00-run.sh — since a live
  `loginctl`/file-drop during testing only touches this device's
  ephemeral /etc//var overlay and doesn't survive a reboot), which makes
  systemd-logind keep that session (and its PipeWire) alive independent
  of any login session's lifecycle.
- That instance also needed the same HDMI-sink selection
  slide-announcer-apply-audio-output always provided for the hand-rolled
  ones — kiosk-start.sh now calls it directly once at kiosk start, since
  nothing had ever pointed the OS's own default PipeWire at HDMI before.

See system/scripts/display-power.py's own docstring for the full
sleep/wake state-transition reasoning this pairs with.
"""
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

# This unit (see slide-announcer-srt-sink.service) has no PAMName/login
# session, so systemd never sets XDG_RUNTIME_DIR for it automatically —
# mpv's --ao=pipewire needs it to find the OS's own default per-user
# PipeWire's socket at /run/user/<uid> (kept alive independent of any
# session by `loginctl enable-linger slideannouncer` — see
# 01-system-files/00-run.sh), same reason kiosk-start.sh exports this
# itself rather than relying on it being present. Confirmed on hardware
# that skipping this makes mpv's PipeWire client fail to connect
# ("Could not connect to context '(null)': Host is down") and play back
# with no audio at all, silently — no error surfaced beyond the journal.
KIOSK_USER = "slideannouncer"

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

SRT_PORT = 7002
POLL_INTERVAL_SECONDS = 2.0
PROBE_TIMEOUT_SECONDS = 5.0
# Pause between mpv exiting and restoring the kiosk/sleep state — gives
# any lingering DRM/PipeWire teardown a moment to finish before the kiosk
# (or nothing, if returning to sleep) tries to claim the display again.
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


def handle_candidate_stream(passphrase):
    if not validate_stream(passphrase):
        return

    was_sleeping = subprocess.run(
        [DISPLAY_POWER_CLI, "status"], capture_output=True, text=True, check=False
    ).stdout.strip() == "sleeping"

    takeover_start = time.monotonic()
    subprocess.run([DISPLAY_POWER_CLI, "takeover"], check=False)
    print(f"[srt-sink] takeover took {time.monotonic() - takeover_start:.1f}s", flush=True)

    hwdec_flags = get_hwdec_flags()
    print(f"[srt-sink] pi model={get_pi_model()} hwdec={hwdec_flags}", flush=True)

    mpv_env = dict(os.environ)
    mpv_env["XDG_RUNTIME_DIR"] = f"/run/user/{pwd.getpwnam(KIOSK_USER).pw_uid}"

    mpv_cmd = [
        MPV_BIN,
        "--no-config",
        "--fs",
        # Legacy, non-GPU DRM output — deliberately not --vo=gpu. Every
        # GPU-accelerated renderer tried on this Pi4/Mesa/v3d stack has
        # now failed in hardware testing, each a different way:
        # --vo=gpu-next (Wayland) leaked a dmabuf fd per frame; --vo=gpu
        # (Wayland) deadlocked completely after its first frame; --vo=gpu
        # --gpu-context=drm (this same direct-DRM setup) decoded and
        # demuxed fine but couldn't present frames fast enough — A-V grew
        # past 14s unbounded while Cache stayed flat (~1.1s, not
        # overflowing) and Dropped never climbed to compensate, so it
        # wasn't a decode bottleneck. --vo=drm costs real CPU (~200-235%,
        # confirmed) doing its scaling/color-conversion via libswscale on
        # the CPU instead of GPU shaders, but it's the ONLY combination
        # that's ever held A-V flush at 0.000 for a whole clip in this
        # investigation — a known, bounded cost beats three different
        # GPU-path failures in a row.
        "--vo=drm",
        # Matches the incoming stream's own resolution (confirmed
        # 1920x1080) rather than the TV's native 4K --drm-mode=preferred —
        # --vo=drm has no hardware scaler at all, so any mismatch here
        # forces an extra CPU scale on top of the conversion it's already
        # doing. Hardcoded because there's no cheap GPU-scaled fallback
        # available for a different resolution the way there would have
        # been if any --vo=gpu variant had actually worked.
        "--drm-mode=1920x1080@60",
        # PipeWire, via the OS's own default per-user instance — kept
        # alive independent of the kiosk (which is stopped right above)
        # by `loginctl enable-linger slideannouncer` — not bare ALSA,
        # which was opening whatever device ALSA considered its default
        # rather than the configured HDMI sink, the likely cause of
        # earlier "no sound" reports.
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
    subprocess.run([DISPLAY_POWER_CLI, "sleep" if was_sleeping else "wake"], check=False)


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
