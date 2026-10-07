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
import time
from collections import deque
from dataclasses import dataclass, field

import captive_portal
import syscmd


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
    subnet_mask: str | None = None
    gateway: str | None = None
    dns_servers: list[str] = field(default_factory=list)
    connectivity: str | None = None  # "full" | "limited" | "portal" | "none" | "unknown"
    # Where a captive portal's sign-in page is, when connectivity == "portal".
    portal_url: str | None = None


def _prefix_to_subnet_mask(prefix: int) -> str:
    mask = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return ".".join(str((mask >> shift) & 0xFF) for shift in (24, 16, 8, 0))


@dataclass
class AccessPoint:
    ssid: str
    signal: int
    security: str  # "" for open networks, else e.g. "WPA2"
    in_use: bool
    # open | owe | wep | wpa | wpa2 | wpa3 | wpa2_wpa3 | enterprise | unknown
    kind: str = "unknown"
    needs_password: bool = True
    # False for networks this UI has no way to join (802.1X needs a
    # username/certificate, WEP is rejected by modern wpa_supplicant).
    supported: bool = True


def classify_security(security: str, wpa_flags: str = "", rsn_flags: str = "") -> str:
    """Kind of network from nmcli's SECURITY / WPA-FLAGS / RSN-FLAGS columns.
    The flags are the reliable part: SECURITY collapses "sae" and "psk" into
    "WPA3" / "WPA2 WPA3" but says nothing useful about OWE ("Enhanced
    Open"), which looks open yet is encrypted and takes no password. RSN
    flags describe WPA2/WPA3; the older WPA-FLAGS are WPA1 only."""
    wpa, rsn = wpa_flags.lower(), rsn_flags.lower()
    if "802.1x" in wpa + rsn:
        return "enterprise"
    has_sae = "sae" in rsn
    has_psk = "psk" in rsn
    if has_sae and has_psk:
        return "wpa2_wpa3"
    if has_sae:
        return "wpa3"
    if has_psk:
        return "wpa2"
    if "owe" in rsn:
        return "owe"
    if "psk" in wpa:
        return "wpa"
    if "WEP" in security:
        return "wep"
    if security.strip() in ("", "--"):
        return "open"
    return "unknown"


def _ap_from_fields(ssid: str, signal: int, security: str, active: bool,
                    wpa_flags: str = "", rsn_flags: str = "") -> AccessPoint:
    kind = classify_security(security, wpa_flags, rsn_flags)
    return AccessPoint(
        ssid=ssid, signal=signal, security=security, in_use=active, kind=kind,
        needs_password=kind not in ("open", "owe"),
        supported=kind not in ("enterprise", "wep"),
    )


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
        return NetworkStatus(connection_type="disconnected", connected=False, connectivity="none")

    device, dev_type, connection = active
    ip_out = await _run(
        "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", device
    )
    ip_addresses = []
    subnet_mask = None
    gateway = None
    dns_servers = []
    for line in ip_out.splitlines():
        if line.startswith("IP4.ADDRESS"):
            addr, _, prefix = _split_terse(line)[1].partition("/")
            ip_addresses.append(addr)
            if subnet_mask is None and prefix.isdigit():
                subnet_mask = _prefix_to_subnet_mask(int(prefix))
        elif line.startswith("IP4.GATEWAY"):
            gateway = _split_terse(line)[1] or None
        elif line.startswith("IP4.DNS"):
            dns = _split_terse(line)[1]
            if dns:
                dns_servers.append(dns)

    try:
        connectivity, portal_url = await check_connectivity()
    except NetworkCommandError:
        connectivity, portal_url = "unknown", None

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
        subnet_mask=subnet_mask,
        gateway=gateway,
        dns_servers=dns_servers,
        connectivity=connectivity,
        portal_url=portal_url,
    )


async def _wifi_device() -> str:
    out = await _run("-t", "-f", "DEVICE,TYPE", "device", "status")
    for line in out.splitlines():
        device, dev_type = _split_terse(line)
        if dev_type == "wifi":
            return device
    raise NetworkCommandError("no WiFi device found")


async def scan_access_points(rescan: bool = True) -> list[AccessPoint]:
    device = await _wifi_device()
    if rescan:
        try:
            await _run("device", "wifi", "rescan", "ifname", device, timeout=10)
            await asyncio.sleep(2)
        except NetworkCommandError:
            # A rescan can be rejected if one just ran recently (NM rate-limits
            # this) — fall through and list whatever NM already has cached.
            pass

    out = await _run(
        "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE,WPA-FLAGS,RSN-FLAGS",
        "device", "wifi", "list", "ifname", device,
    )
    seen: dict[str, AccessPoint] = {}
    for line in out.splitlines():
        if not line:
            continue
        fields = _split_terse(line)
        if len(fields) != 6:
            continue
        ssid, signal, security, active, wpa_flags, rsn_flags = fields
        if not ssid:
            continue  # hidden networks broadcasting a blank SSID
        signal_val = int(signal) if signal.isdigit() else 0
        # Same SSID can appear once per BSSID — keep the strongest signal.
        existing = seen.get(ssid)
        if existing is None or signal_val > existing.signal:
            seen[ssid] = _ap_from_fields(
                ssid, signal_val, security, active == "yes", wpa_flags, rsn_flags
            )
    return sorted(seen.values(), key=lambda ap: ap.signal, reverse=True)


class ConnectError(NetworkCommandError):
    """A failed join, with what's known about why. `str()` is a plain-English
    message (also the API's `detail`); `reason` is a stable code the UI
    translates, `raw` is nmcli's own text, and `log` the NetworkManager /
    wpa_supplicant journal lines from the attempt (empty if unreadable)."""

    def __init__(self, message: str, reason: str, raw: str, log: list[str], log_restricted: bool):
        super().__init__(message)
        self.reason = reason
        self.raw = raw
        self.log = log
        self.log_restricted = log_restricted


# Plain-English fallbacks; the frontend swaps in translated text by `reason`.
FAILURE_MESSAGES = {
    "wrong_password": "The password was rejected. Check it and try again.",
    "not_found": "That network wasn't found — it may be out of range or have just gone off.",
    "ap_full": "The router refused the connection (it may have too many devices connected).",
    "auth_rejected": "The router rejected this device's connection request.",
    "dhcp_failed": "Connected to the router, but it never handed out an IP address.",
    "timeout": "The connection took too long and was abandoned.",
    "radio_off": "The Wi-Fi radio is switched off or blocked.",
    "unsupported": "This kind of network (WEP or company login) isn't supported on this device.",
    "unknown": "The connection failed for an unrecognized reason.",
}

# Most specific evidence first. wpa_supplicant's journal lines say exactly
# why; nmcli's own message is the fallback when the journal can't be read.
_LOG_RULES = [
    (re.compile(r"CTRL-EVENT-NETWORK-NOT-FOUND"), "not_found"),
    (re.compile(r"WRONG_KEY|pre-shared key may be incorrect|4-Way Handshake failed", re.I), "wrong_password"),
    (re.compile(r"ASSOC-REJECT.*status_code=17"), "ap_full"),
    (re.compile(r"CTRL-EVENT-(ASSOC|AUTH)-REJECT|SME: Trying to authenticate.*failed"), "auth_rejected"),
    (re.compile(r"dhcp4.*(timed out|failed|expired)|DHCP.*(timeout|failed)", re.I), "dhcp_failed"),
]
_NMCLI_RULES = [
    (re.compile(r"No network with SSID"), "not_found"),
    (re.compile(r"Secrets were required|password.*(wrong|incorrect)", re.I), "wrong_password"),
    (re.compile(r"IP configuration|ip-config", re.I), "dhcp_failed"),
    (re.compile(r"took too long|timed out|Timeout", re.I), "timeout"),
    (re.compile(r"rfkill|not ready|radio.*(off|disabled)|unavailable", re.I), "radio_off"),
    (re.compile(r"802-1x|\bwep\b", re.I), "unsupported"),
]


def classify_failure(raw: str, log: list[str], kind: str | None = None) -> str:
    """Reason code for a failed join from nmcli's message, the journal
    lines of the attempt and (if known) the network's security kind."""
    if kind in ("enterprise", "wep"):
        return "unsupported"
    text = "\n".join(log)
    for pattern, reason in _LOG_RULES:
        if pattern.search(text):
            return reason
    for pattern, reason in _NMCLI_RULES:
        if pattern.search(raw):
            return reason
    return "unknown"


# Recent join attempts, newest last, for the diagnostics screen — in
# memory only: it answers "what just happened", not "what ever happened".
attempts: deque[dict] = deque(maxlen=20)


def _record_attempt(ssid: str, kind: str | None, ok: bool, reason: str | None, raw: str | None) -> None:
    attempts.append({
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ssid": ssid, "security_kind": kind, "ok": ok, "reason": reason, "raw": raw,
    })


async def _lookup_kind(ssid: str) -> str | None:
    """Security kind of `ssid` from NM's cached scan (no rescan — the
    list was just on screen)."""
    try:
        return next((ap.kind for ap in await scan_access_points(rescan=False) if ap.ssid == ssid), None)
    except NetworkCommandError:
        return None


async def connect(ssid: str, password: str | None) -> None:
    """Raises ConnectError (a NetworkCommandError) on failure, carrying a
    reason code, nmcli's own message and the journal lines of the attempt
    (bad password, SSID out of range, router rejecting us, no DHCP, …).
    """
    device = await _wifi_device()
    kind = await _lookup_kind(ssid)
    started = syscmd.now()

    # `nmcli device wifi connect` reuses an existing connection profile for
    # this SSID by name rather than always creating a fresh one. Confirmed
    # by testing: a failed attempt (wrong password) can leave behind a
    # broken profile — NetworkManager creates it before the WPA handshake
    # fails, and can't re-prompt for secrets in this non-interactive
    # context ("nmcli cannot ask without '--ask' option") — and every
    # subsequent attempt then reuses THAT broken profile instead of a
    # clean one, failing a different way each time (up to and including
    # "802-11-wireless-security.key-mgmt: property is missing" once the
    # profile is malformed enough). Delete any existing profile for this
    # SSID first so every explicit connect attempt from the UI starts from
    # a clean slate — best-effort, since the common case (first-ever
    # attempt) has nothing to delete.
    try:
        await _run("connection", "delete", ssid)
    except NetworkCommandError:
        pass

    args = ["device", "wifi", "connect", ssid, "ifname", device]
    if password:
        args += ["password", password]
    try:
        await _run(*args, timeout=30)
    except NetworkCommandError as exc:
        raw = str(exc)
        log, restricted = await syscmd.journal(since_epoch=started - 1)
        reason = classify_failure(raw, log or [], kind)
        # The failed attempt leaves a half-built profile that NM would keep
        # retrying (and that the next attempt would trip over) — drop it.
        try:
            await _run("connection", "delete", ssid)
        except NetworkCommandError:
            pass
        _record_attempt(ssid, kind, False, reason, raw)
        raise ConnectError(FAILURE_MESSAGES[reason], reason, raw, log or [], restricted) from exc
    _record_attempt(ssid, kind, True, None, None)


async def check_connectivity() -> tuple[str, str | None]:
    """(state, portal_url) — state is one of NetworkManager's own
    full | limited | portal | none. NM's answer alone can't be trusted for
    "full": this image never configures NM's connectivity check URI, and
    without one NM says "full" for any connection with a default route,
    captive portal or not. So a "full" (or a "portal", for its URL) is
    double-checked with captive_portal.probe()."""
    nm_state = (await _run("networking", "connectivity", "check", timeout=10)).strip()
    if nm_state not in ("full", "portal"):
        return nm_state, None
    result = await captive_portal.probe()
    if result["state"] == "portal":
        return "portal", result["portal_url"]
    if result["state"] == "none":
        # Connected with a route, but the probe got nowhere — no way out
        # to the internet (or that one host is blocked).
        return "limited", None
    return "full", None


async def forget(ssid: str) -> None:
    await _run("connection", "delete", ssid)
