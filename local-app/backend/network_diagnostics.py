"""Everything Settings > Network > Diagnostics shows, in one report.

collect() gathers each layer independently and concurrently — radio and
driver, saved profiles, what's in the air, addressing, the path out
(gateway → internet by IP → DNS → captive portal → the slide server), the
clock, recent connect attempts and the NetworkManager/wpa_supplicant
journal — and build_findings() boils that down to a short list of
plain-language problems, each a stable `code` the frontend translates.

Read-only and gentle on a live connection: it never rescans unless asked
(a rescan can briefly stall a WiFi link that's streaming video) and every
section fails on its own — a missing `iw` or an unreadable journal leaves
that one section empty with an `error`, not the whole report.
"""
import asyncio
import json
import re
import socket
import time
from urllib.parse import urlsplit

import captive_portal
import network
import pairing
import server_check
import syscmd

INTERNET_IP = "1.1.1.1"  # reachability by address, independent of DNS
SIGNAL_WEAK_PERCENT = 35
WPA3_MIN_NM = (1, 20)
WPA3_MIN_SUPPLICANT = (2, 9)


def _terse_fields(text: str) -> dict[str, str]:
    """`nmcli -t -f A,B device show` output ("A:value" lines) as a dict."""
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key] = value.replace("\\:", ":").strip()
    return out


def _version(text: str) -> tuple[int, ...] | None:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text)
    return tuple(int(g) for g in match.groups() if g is not None) if match else None


def _band(freq_mhz: int | None) -> str | None:
    if not freq_mhz:
        return None
    return "2.4 GHz" if freq_mhz < 3000 else "5 GHz" if freq_mhz < 5925 else "6 GHz"


async def _safe(coro):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 - a section's failure must not sink the report
        return {"error": f"{type(exc).__name__}: {exc}"}


# --- sections ----------------------------------------------------------

async def _system() -> dict:
    nm, supplicant, general, tz, reg, rfkill = await asyncio.gather(
        syscmd.run("nmcli", "--version"),
        syscmd.run("wpa_supplicant", "-v"),
        syscmd.run("nmcli", "-t", "-f", "RUNNING,STATE,CONNECTIVITY,WIFI-HW,WIFI", "general", "status"),
        syscmd.run("timedatectl", "show", "-p", "NTPSynchronized", "-p", "Timezone"),
        syscmd.run("iw", "reg", "get"),
        syscmd.run("rfkill", "-J"),
    )
    nm_version = _version(nm.stdout)
    supplicant_version = _version(supplicant.stdout)

    country = None
    if reg.ok:
        match = re.search(r"country (\w\w)", reg.stdout)
        country = match.group(1) if match else None

    blocked = []
    if rfkill.ok:
        try:
            parsed = json.loads(rfkill.stdout)
            # Older rfkill names the list "" instead of "rfkilldevices".
            for dev in parsed.get("rfkilldevices") or parsed.get("") or []:
                if dev.get("type") == "wlan" and "blocked" in (dev.get("soft"), dev.get("hard")):
                    blocked.append({"device": dev.get("device"), "soft": dev.get("soft"), "hard": dev.get("hard")})
        except (ValueError, AttributeError):
            pass

    general_fields = {}
    if general.ok and general.stdout.strip():
        parts = general.stdout.strip().split(":")
        if len(parts) == 5:
            general_fields = dict(zip(("running", "state", "connectivity", "wifi_hw", "wifi"), parts))

    time_fields = _terse_fields(tz.stdout.replace("=", ":")) if tz.ok else {}
    return {
        "networkmanager": nm.stdout.strip().rsplit(" ", 1)[-1] if nm.ok else None,
        "wpa_supplicant": supplicant.stdout.splitlines()[0].replace("wpa_supplicant ", "") if supplicant.ok and supplicant.stdout else None,
        "wpa3_software": bool(
            nm_version and supplicant_version
            and nm_version[:2] >= WPA3_MIN_NM and supplicant_version[:2] >= WPA3_MIN_SUPPLICANT
        ),
        "general": general_fields,
        "regulatory_country": country,
        "rfkill_blocked": blocked,
        "ntp_synchronized": (time_fields.get("NTPSynchronized") == "yes") if time_fields else None,
        "timezone": time_fields.get("Timezone"),
        "clock": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    }


async def _radio() -> dict:
    try:
        device = await network._wifi_device()
    except network.NetworkCommandError as exc:
        return {"present": False, "error": str(exc)}

    show, link, info, driver = await asyncio.gather(
        syscmd.run("nmcli", "-t", "-f",
                   "GENERAL.DRIVER,GENERAL.STATE,GENERAL.REASON,GENERAL.HWADDR,GENERAL.MTU,WIFI-PROPERTIES",
                   "device", "show", device),
        syscmd.run("iw", "dev", device, "link"),
        syscmd.run("iw", "dev", device, "info"),
        syscmd.run("ethtool", "-i", device),
    )
    fields = _terse_fields(show.stdout)

    link_info = {}
    if link.ok and "Not connected" not in link.stdout:
        for key, label in (("freq", "freq"), ("signal", "signal_dbm"), ("tx bitrate", "tx_bitrate"),
                           ("rx bitrate", "rx_bitrate"), ("SSID", "ssid"), ("Connected to", "bssid")):
            match = re.search(rf"^\s*{key}:?\s+(.+)$", link.stdout, re.M)
            if match:
                link_info[label] = match.group(1).strip()
        if "freq" in link_info:
            try:
                link_info["band"] = _band(int(float(link_info["freq"])))
            except ValueError:
                pass
        if "bssid" in link_info:
            link_info["bssid"] = link_info["bssid"].split()[0]

    channel_width = None
    if info.ok:
        match = re.search(r"width:\s*(\d+) MHz", info.stdout)
        channel_width = int(match.group(1)) if match else None

    firmware = None
    if driver.ok:
        match = re.search(r"^firmware-version:\s*(.+)$", driver.stdout, re.M)
        firmware = match.group(1).strip() if match else None

    return {
        "present": True,
        "device": device,
        "driver": fields.get("GENERAL.DRIVER"),
        "firmware": firmware,
        "state": fields.get("GENERAL.STATE"),
        "reason": fields.get("GENERAL.REASON"),
        "mac": fields.get("GENERAL.HWADDR"),
        "supports": {
            "2ghz": fields.get("WIFI-PROPERTIES.2GHZ") == "yes",
            "5ghz": fields.get("WIFI-PROPERTIES.5GHZ") == "yes",
            "6ghz": fields.get("WIFI-PROPERTIES.6GHZ") == "yes",
        },
        "link": link_info,
        "channel_width_mhz": channel_width,
    }


async def _profiles() -> list[dict]:
    """Saved WiFi profiles (no secrets — nmcli hides them without -s)."""
    listing = await syscmd.run("nmcli", "-t", "-f", "UUID,NAME,TYPE,AUTOCONNECT", "connection", "show")
    if not listing.ok:
        return []
    profiles = []
    for line in listing.stdout.splitlines():
        parts = network._split_terse(line)
        if len(parts) == 4 and parts[2] == "802-11-wireless":
            profiles.append({"uuid": parts[0], "name": parts[1], "autoconnect": parts[3] == "yes"})
    profiles = profiles[:10]

    async def detail(profile: dict) -> dict:
        # Asking for the whole security group: nmcli prints nothing for an
        # open profile, so any line at all means the profile is secured.
        security, wireless = await asyncio.gather(
            syscmd.run("nmcli", "-t", "-f", "802-11-wireless-security", "connection", "show", profile["uuid"]),
            syscmd.run("nmcli", "-t", "-f", "802-11-wireless.hidden", "connection", "show", profile["uuid"]),
        )
        sec = _terse_fields(security.stdout)
        key_mgmt = sec.get("802-11-wireless-security.key-mgmt") or None
        return {
            **profile,
            "key_mgmt": key_mgmt,
            "pmf": sec.get("802-11-wireless-security.pmf"),
            "hidden": _terse_fields(wireless.stdout).get("802-11-wireless.hidden") == "yes",
            # A secured profile with no key-mgmt is the half-built leftover of
            # a failed join (see network.connect's comment): it can never work.
            "broken": bool(sec) and key_mgmt is None,
        }

    return list(await asyncio.gather(*(detail(p) for p in profiles)))


async def _nearby(rescan: bool) -> dict:
    device = await network._wifi_device()
    if rescan:
        await syscmd.run("nmcli", "device", "wifi", "rescan", "ifname", device, timeout=10)
        await asyncio.sleep(3)
    res = await syscmd.run(
        "nmcli", "-t", "-f", "BSSID,SSID,CHAN,FREQ,RATE,SIGNAL,SECURITY,WPA-FLAGS,RSN-FLAGS,ACTIVE",
        "device", "wifi", "list", "ifname", device)
    if not res.ok:
        return {"error": res.stderr.strip() or "scan list unavailable", "networks": []}

    by_ssid: dict[str, dict] = {}
    hidden = 0
    for line in res.stdout.splitlines():
        f = network._split_terse(line)
        if len(f) != 10:
            continue
        bssid, ssid, chan, freq, rate, signal, security, wpa, rsn, active = f
        if not ssid:
            hidden += 1
            continue
        freq_mhz = int(freq.split()[0]) if freq.split() and freq.split()[0].isdigit() else None
        signal_val = int(signal) if signal.isdigit() else 0
        entry = by_ssid.setdefault(ssid, {
            "ssid": ssid, "security": security,
            "kind": network.classify_security(security, wpa, rsn),
            "signal": 0, "in_use": False, "access_points": [],
        })
        entry["access_points"].append({
            "bssid": bssid, "channel": int(chan) if chan.isdigit() else None,
            "freq": freq_mhz, "band": _band(freq_mhz), "rate": rate, "signal": signal_val,
            "active": active == "yes",
        })
        entry["signal"] = max(entry["signal"], signal_val)
        entry["in_use"] = entry["in_use"] or active == "yes"
    networks = sorted(by_ssid.values(), key=lambda n: n["signal"], reverse=True)[:40]
    return {"networks": networks, "hidden_count": hidden, "rescanned": rescan}


async def _ping(host: str) -> dict:
    res = await syscmd.run("ping", "-n", "-c", "3", "-W", "2", host, timeout=10)
    if res.rc is None:
        return {"host": host, "ok": None, "detail": "ping not available"}
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", res.stdout)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", res.stdout)
    loss_pct = float(loss.group(1)) if loss else 100.0
    return {"host": host, "ok": loss_pct < 100, "loss_percent": loss_pct,
            "avg_ms": float(rtt.group(1)) if rtt else None}


async def _resolve(host: str) -> dict:
    started = time.monotonic()
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM), timeout=5)
    except (OSError, asyncio.TimeoutError) as exc:
        return {"host": host, "ok": False, "error": type(exc).__name__,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}
    return {"host": host, "ok": True, "addresses": sorted({i[4][0] for i in infos})[:4],
            "elapsed_ms": round((time.monotonic() - started) * 1000)}


async def _path(status: network.NetworkStatus) -> dict:
    """Walk outward from the router: gateway → internet by IP → DNS →
    HTTP (captive portal?) → the slide server."""
    server_host = None
    try:
        server_host = urlsplit(pairing.read_server_url()).hostname
    except pairing.PairingError:
        pass

    jobs = [
        _ping(status.gateway) if status.gateway else asyncio.sleep(0),
        _ping(INTERNET_IP),
        _resolve(urlsplit(captive_portal.PROBE_URL).hostname),
        captive_portal.probe(attempts=1),
        server_check.check(),
        _resolve(server_host) if server_host else asyncio.sleep(0),
    ]
    gw, internet_ip, dns, http, server, server_dns = await asyncio.gather(*(_safe(j) for j in jobs))
    return {
        "gateway": gw if status.gateway else None,
        "internet_ip": internet_ip,
        "dns": dns,
        "http": http,
        "server": server,
        "server_dns": server_dns if server_host else None,
    }


async def _log() -> dict:
    lines, restricted = await syscmd.journal(lines=100)
    return {"available": lines is not None, "restricted": restricted, "lines": lines or []}


# --- report ------------------------------------------------------------

async def collect(rescan: bool = False) -> dict:
    status_err = None
    try:
        status = await network.get_status()
    except network.NetworkCommandError as exc:
        status, status_err = network.NetworkStatus(connection_type="disconnected", connected=False,
                                                    connectivity="unknown"), str(exc)

    system, radio, profiles, nearby, path, log = await asyncio.gather(
        _safe(_system()), _safe(_radio()), _safe(_profiles()), _safe(_nearby(rescan)),
        _safe(_path(status)) if status.connected else _safe(asyncio.sleep(0, result=None)),
        _safe(_log()),
    )
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "status_error": status_err,
        "system": system,
        "radio": radio,
        "profiles": profiles,
        "nearby": nearby,
        "path": path,
        "attempts": list(network.attempts),
        "log": log,
    }
    report["findings"] = build_findings(report)
    return report


def _get(d, *keys):
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def build_findings(report: dict) -> list[dict]:
    """Plain-language problems worst-first. Each is {severity, code,
    params}; the frontend owns the wording."""
    findings: list[dict] = []

    def add(severity: str, code: str, **params):
        findings.append({"severity": severity, "code": code, "params": params})

    status = report["status"]
    system = report["system"] if isinstance(report["system"], dict) else {}
    radio = report["radio"] if isinstance(report["radio"], dict) else {}
    path = report["path"] if isinstance(report["path"], dict) else None

    if report.get("status_error"):
        add("error", "networkmanager_unreachable", detail=report["status_error"])
    if radio.get("present") is False:
        add("error", "no_wifi_device")
    if system.get("rfkill_blocked"):
        add("error", "radio_blocked")
    elif _get(system, "general", "wifi") == "disabled":
        add("error", "wifi_disabled")
    if system.get("regulatory_country") == "00":
        add("warn", "regdomain_unset")

    broken = [p["name"] for p in report["profiles"] if isinstance(p, dict) and p.get("broken")]
    if broken:
        add("warn", "broken_profile", names=broken)

    last = report["attempts"][-1] if report["attempts"] else None
    if last and not last["ok"]:
        add("warn", "last_connect_failed", ssid=last["ssid"], reason=last["reason"], kind=last["security_kind"])

    if not status.connected:
        add("warn", "not_connected")
    else:
        if status.signal is not None and status.signal < SIGNAL_WEAK_PERCENT:
            add("warn", "weak_signal", signal=status.signal)
        if status.connectivity == "portal":
            add("warn", "captive_portal", url=status.portal_url)
        if path:
            gateway = path.get("gateway") or {}
            internet = path.get("internet_ip") or {}
            dns = path.get("dns") or {}
            http = path.get("http") or {}
            server = path.get("server") or {}
            if gateway.get("ok") is False:
                add("error", "gateway_unreachable", gateway=gateway.get("host"))
            elif status.connectivity != "portal":
                if internet.get("ok") is False:
                    add("error", "no_route_to_internet")
                elif dns.get("ok") is False:
                    add("error", "dns_failing")
                elif http.get("state") == "none":
                    add("warn", "http_blocked", error=http.get("error"))
            if not status.dns_servers:
                add("warn", "no_dns_servers")
            if server.get("state") in ("unreachable", "error") and status.connectivity == "full":
                add("warn", "server_unreachable", detail=server.get("detail"))
            if system.get("ntp_synchronized") is False and server.get("state") == "unreachable":
                add("warn", "clock_unsynced")

    nets = _get(report, "nearby", "networks") or []
    if system.get("wpa3_software") is False:
        add("info", "wpa3_software_old", networkmanager=system.get("networkmanager"),
            wpa_supplicant=system.get("wpa_supplicant"))
    unsupported = [n["ssid"] for n in nets if n["kind"] in ("enterprise", "wep")]
    if unsupported:
        add("info", "unsupported_networks_nearby", names=unsupported[:5])

    order = {"error": 0, "warn": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return findings
