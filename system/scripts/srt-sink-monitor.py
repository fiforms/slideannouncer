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

See system/scripts/display-power.py's own docstring for the full
sleep/wake state-transition reasoning this pairs with.
"""
import json
import select
import socket
import subprocess
import time
import os
import platform
from pathlib import Path
from urllib.parse import quote

SRT_SINK_CONFIG = Path("/data/status/srt-sink.json")
DISPLAY_POWER_CLI = "/usr/local/sbin/slide-announcer-display-power"
MPV_BIN = "mpv"

SRT_PORT = 7002
POLL_INTERVAL_SECONDS = 2.0
PROBE_TIMEOUT_SECONDS = 5.0
# Pause between mpv exiting and restoring the kiosk/sleep state — gives
# any lingering DRM/ALSA teardown a moment to finish before the kiosk (or
# nothing, if returning to sleep) tries to claim the display again.
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
        # Pi 4 / 3 have a physical H.264 block using V4L2 M2M
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

    # Base low-latency DRM arguments
    mpv_cmd = [
        MPV_BIN,
        "--no-config",
        "--fs",
        # DRM & Output Overrides — matches the incoming stream's own
        # resolution (confirmed 1920x1080 via journal: "Video --vid=1
        # (h264 1920x1080)") rather than the TV's native 4K mode. Any
        # mismatch here forces mpv's DRM vo to scale in software every
        # frame ("VO: Direct Rendering Manager (software scaling)" in the
        # journal) even though hwdec decode itself succeeds — that
        # software scale, not decode, was the real CPU/lag cause. Letting
        # the TV upscale the 1080p HDMI signal itself is free.
        "--vo=drm",
        "--gpu-context=drm",
        "--drm-mode=1920x1080@60",
        # Real-Time Sync — paced to realtime instead of --untimed/desync.
        # Letting the decode loop free-run was the likely cause of the
        # high CPU + growing lag: if decode ever dips below the stream's
        # real-time rate, mpv should drop frames to keep pace rather than
        # racing to catch up.
        "--ao=alsa",
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
    result = subprocess.run(mpv_cmd, check=False)
    elapsed = time.monotonic() - start
    print(f"[srt-sink] mpv exited code={result.returncode} after {elapsed:.1f}s", flush=True)

    time.sleep(CLEANUP_DELAY_SECONDS)
    subprocess.run([DISPLAY_POWER_CLI, "sleep" if was_sleeping else "wake"], check=False)


def main():
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
