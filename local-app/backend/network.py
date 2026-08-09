"""NetworkManager control via `nmcli` subprocess calls (see SLIDE_ANNOUNCER.md,
"First-boot / WiFi setup flow": nmcli was chosen over raw D-Bus bindings for
stability/testability). Runs as the `slideannouncer` service user, which the
polkit rule in system/polkit/50-networkmanager-slide-announcer.rules
authorizes for full NetworkManager D-Bus control.

Every nmcli call goes through `_run`/`_run_async`, both wrapped so a missing
`nmcli` binary (any non-Linux dev machine, or a container without
NetworkManager) degrades to a clear error instead of an unhandled exception.
"""
import asyncio
import re
from dataclasses import dataclass, field


class NetworkCommandError(RuntimeError):
    """`nmcli` ran but returned a non-zero exit status."""


async def _run(*args: str, timeout: float = 15.0) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            "nmcli", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except FileNotFoundError as exc:
        raise NetworkCommandError("nmcli is not installed on this system") from exc
    except asyncio.TimeoutError as exc:
        proc.kill()
        raise NetworkCommandError(f"nmcli {' '.join(args)} timed out") from exc

    if proc.returncode != 0:
        raise NetworkCommandError(stderr.decode(errors="replace").strip() or "nmcli command failed")
    return stdout.decode(errors="replace")


def _split_terse(line: str) -> list[str]:
    """Split a `nmcli -t` colon-separated line, honoring nmcli's `\\:` escape."""
    return [field.replace("\\:", ":") for field in re.split(r"(?<!\\):", line)]


@dataclass
class NetworkStatus:
    connection_type: str  # "wifi" | "ethernet" | "disconnected"
    connected: bool
    ssid: str | None = None
    signal: int | None = None
    ip_addresses: list[str] = field(default_factory=list)
    device: str | None = None


@dataclass
class AccessPoint:
    ssid: str
    signal: int
    security: str  # "" for open networks, else e.g. "WPA2"
    in_use: bool


async def get_status() -> NetworkStatus:
    out = await _run("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")
    active = None
    for line in out.splitlines():
        if not line:
            continue
        device, dev_type, state, connection = _split_terse(line)
        if dev_type in ("wifi", "ethernet") and state == "connected":
            active = (device, dev_type, connection)
            break

    if active is None:
        return NetworkStatus(connection_type="disconnected", connected=False)

    device, dev_type, connection = active
    ip_out = await _run("-t", "-f", "IP4.ADDRESS", "device", "show", device)
    ip_addresses = [
        _split_terse(line)[1].split("/")[0]
        for line in ip_out.splitlines()
        if line.startswith("IP4.ADDRESS")
    ]

    ssid = None
    signal = None
    if dev_type == "wifi":
        ssid = connection
        ap_out = await _run(
            "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "ifname", device
        )
        for line in ap_out.splitlines():
            fields = _split_terse(line)
            if len(fields) == 3 and fields[0] == "yes":
                signal = int(fields[2]) if fields[2].isdigit() else None
                break

    return NetworkStatus(
        connection_type=dev_type,
        connected=True,
        ssid=ssid,
        signal=signal,
        ip_addresses=ip_addresses,
        device=device,
    )


async def _wifi_device() -> str:
    out = await _run("-t", "-f", "DEVICE,TYPE", "device", "status")
    for line in out.splitlines():
        device, dev_type = _split_terse(line)
        if dev_type == "wifi":
            return device
    raise NetworkCommandError("no WiFi device found")


async def scan_access_points() -> list[AccessPoint]:
    device = await _wifi_device()
    try:
        await _run("device", "wifi", "rescan", "ifname", device, timeout=10)
        await asyncio.sleep(2)
    except NetworkCommandError:
        # A rescan can be rejected if one just ran recently (NM rate-limits
        # this) — fall through and list whatever NM already has cached.
        pass

    out = await _run(
        "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE", "device", "wifi", "list", "ifname", device
    )
    seen: dict[str, AccessPoint] = {}
    for line in out.splitlines():
        if not line:
            continue
        fields = _split_terse(line)
        if len(fields) != 4:
            continue
        ssid, signal, security, active = fields
        if not ssid:
            continue  # hidden networks broadcasting a blank SSID
        signal_val = int(signal) if signal.isdigit() else 0
        # Same SSID can appear once per BSSID — keep the strongest signal.
        existing = seen.get(ssid)
        if existing is None or signal_val > existing.signal:
            seen[ssid] = AccessPoint(
                ssid=ssid, signal=signal_val, security=security, in_use=(active == "yes")
            )
    return sorted(seen.values(), key=lambda ap: ap.signal, reverse=True)


async def connect(ssid: str, password: str | None) -> None:
    """Raises NetworkCommandError with nmcli's own message on failure
    (bad password, SSID out of range, etc.) — passed through to the UI.
    """
    device = await _wifi_device()
    args = ["device", "wifi", "connect", ssid, "ifname", device]
    if password:
        args += ["password", password]
    await _run(*args, timeout=30)


async def check_connectivity() -> str:
    """One of NetworkManager's own states: full | limited | portal | none."""
    out = await _run("networking", "connectivity", "check", timeout=10)
    return out.strip()


async def forget(ssid: str) -> None:
    await _run("connection", "delete", ssid)
