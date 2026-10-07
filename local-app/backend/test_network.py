"""Tests for network.py's security/failure classification and connect error
path, captive_portal's sign-in URL choice, and network_diagnostics'
findings. No NetworkManager: nmcli and the journal are faked.
"""
import asyncio

import pytest

import captive_portal
import network
import network_diagnostics as diag
import syscmd


# --- security classification -------------------------------------------

@pytest.mark.parametrize("security,wpa,rsn,kind", [
    ("", "(none)", "(none)", "open"),
    ("WPA2", "(none)", "pair_ccmp group_ccmp psk", "wpa2"),
    ("WPA3", "(none)", "pair_ccmp group_ccmp sae", "wpa3"),
    ("WPA2 WPA3", "(none)", "pair_ccmp group_ccmp psk sae", "wpa2_wpa3"),
    ("WPA1", "pair_tkip group_tkip psk", "(none)", "wpa"),
    ("WPA2 802.1X", "(none)", "pair_ccmp group_ccmp 802.1x", "enterprise"),
    ("OWE", "(none)", "pair_ccmp group_ccmp owe", "owe"),
    ("WEP", "(none)", "(none)", "wep"),
])
def test_classify_security(security, wpa, rsn, kind):
    assert network.classify_security(security, wpa, rsn) == kind


def test_open_and_owe_need_no_password_and_enterprise_is_unsupported():
    assert not network._ap_from_fields("cafe", 70, "", False).needs_password
    assert not network._ap_from_fields("o", 70, "OWE", False, "(none)", "owe").needs_password
    wpa3 = network._ap_from_fields("w", 70, "WPA3", False, "(none)", "sae")
    assert wpa3.needs_password and wpa3.supported
    corp = network._ap_from_fields("c", 70, "WPA2 802.1X", False, "(none)", "802.1x")
    assert not corp.supported


# --- failure classification --------------------------------------------

@pytest.mark.parametrize("raw,log,kind,reason", [
    ("Error: Connection activation failed: (7) Secrets were required, but not provided.", [], None, "wrong_password"),
    ("whatever", ["wpa_supplicant: wlan0: CTRL-EVENT-SSID-TEMP-DISABLED id=0 reason=WRONG_KEY"], None, "wrong_password"),
    ("Error: No network with SSID 'x' found.", [], None, "not_found"),
    ("whatever", ["wpa_supplicant: CTRL-EVENT-ASSOC-REJECT bssid=aa status_code=17"], None, "ap_full"),
    ("whatever", ["wpa_supplicant: CTRL-EVENT-ASSOC-REJECT bssid=aa status_code=1"], None, "auth_rejected"),
    ("whatever", ["NetworkManager: dhcp4 (wlan0): request timed out"], None, "dhcp_failed"),
    ("(5) IP configuration could not be reserved", [], None, "dhcp_failed"),
    ("Connection activation took too long", [], None, "timeout"),
    ("???", [], None, "unknown"),
    ("Secrets were required", [], "enterprise", "unsupported"),
])
def test_classify_failure(raw, log, kind, reason):
    assert network.classify_failure(raw, log, kind) == reason


def test_log_evidence_beats_the_generic_nmcli_message():
    # nmcli says "Secrets were required" for most failures; the journal knows better.
    assert network.classify_failure(
        "Secrets were required, but not provided",
        ["wpa_supplicant: CTRL-EVENT-ASSOC-REJECT status_code=17"],
    ) == "ap_full"


# --- connect() ---------------------------------------------------------

def fake_nmcli(monkeypatch, connect_error=None, scan=""):
    calls = []

    async def run(*args, timeout=15.0):
        calls.append(args)
        if args[:2] == ("-t", "-f") and "DEVICE,TYPE" in args:
            return "wlan0:wifi\n"
        if "list" in args:
            return scan
        if args[:3] == ("device", "wifi", "connect") and connect_error:
            raise network.NetworkCommandError(connect_error)
        return ""

    monkeypatch.setattr(network, "_run", run)
    network.attempts.clear()
    return calls


def test_scan_dedupes_per_ssid_and_classifies(monkeypatch):
    scan = (
        "Home:80:WPA2 WPA3:yes:(none):pair_ccmp group_ccmp psk sae\n"
        "Home:40:WPA2 WPA3:no:(none):pair_ccmp group_ccmp psk sae\n"
        "Corp:60:WPA2 802.1X:no:(none):pair_ccmp group_ccmp 802.1x\n"
        ":50:WPA2:no:(none):pair_ccmp group_ccmp psk\n"
    )
    fake_nmcli(monkeypatch, scan=scan)
    aps = asyncio.run(network.scan_access_points(rescan=False))
    assert [(a.ssid, a.kind, a.supported) for a in aps] == [
        ("Home", "wpa2_wpa3", True), ("Corp", "enterprise", False)]
    assert aps[0].in_use


def test_failed_connect_reports_reason_log_and_cleans_up(monkeypatch):
    calls = fake_nmcli(monkeypatch, connect_error="Error: Connection activation failed: (7) Secrets were required",
                       scan="Home:80:WPA3:no:(none):pair_ccmp group_ccmp sae\n")

    async def journal(since_epoch=None, lines=120):
        return ["12:00:00 wpa_supplicant: wlan0: CTRL-EVENT-SSID-TEMP-DISABLED reason=WRONG_KEY"], False

    monkeypatch.setattr(syscmd, "journal", journal)
    with pytest.raises(network.ConnectError) as caught:
        asyncio.run(network.connect("Home", "hunter2"))
    err = caught.value
    assert err.reason == "wrong_password"
    assert "WRONG_KEY" in err.log[0] and "Secrets were required" in err.raw
    # profile deleted before the attempt and again after the failure
    assert [c for c in calls if c[:2] == ("connection", "delete")] == [("connection", "delete", "Home")] * 2
    assert network.attempts[-1]["ssid"] == "Home" and network.attempts[-1]["security_kind"] == "wpa3"
    assert not network.attempts[-1]["ok"]


def test_successful_connect_is_recorded(monkeypatch):
    fake_nmcli(monkeypatch)
    asyncio.run(network.connect("Home", "pw"))
    assert network.attempts[-1]["ok"]


# --- captive portal ----------------------------------------------------

def sign_in_url(monkeypatch, probe_result, gateway):
    async def probe(attempts=2):
        return probe_result

    async def watch(return_url):
        return

    monkeypatch.setattr(captive_portal, "probe", probe)
    monkeypatch.setattr(captive_portal, "_watch", watch)
    return asyncio.run(captive_portal.start_sign_in("/settings/network", gateway))


def test_sign_in_prefers_the_portals_own_redirect(monkeypatch):
    url = sign_in_url(monkeypatch, {"state": "portal", "portal_url": "http://login.hotel/x"}, "10.0.0.1")
    assert url == "http://login.hotel/x"


def test_sign_in_falls_back_to_the_gateway_when_the_portal_blocks_the_probe(monkeypatch):
    url = sign_in_url(monkeypatch, {"state": "none", "portal_url": None}, "10.0.0.1")
    assert url == "http://10.0.0.1/"


def test_sign_in_falls_back_to_the_probe_url_without_a_gateway(monkeypatch):
    url = sign_in_url(monkeypatch, {"state": "none", "portal_url": None}, None)
    assert url == captive_portal.PROBE_URL


# --- findings ----------------------------------------------------------

def report(**over):
    base = {
        "status": network.NetworkStatus(connection_type="wifi", connected=True, ssid="x", signal=80,
                                        dns_servers=["10.0.0.1"], connectivity="full", gateway="10.0.0.1"),
        "status_error": None,
        "system": {"regulatory_country": "US", "rfkill_blocked": [], "general": {"wifi": "enabled"},
                   "wpa3_software": True, "ntp_synchronized": True},
        "radio": {"present": True},
        "profiles": [], "attempts": [],
        "nearby": {"networks": []},
        "path": {"gateway": {"host": "10.0.0.1", "ok": True}, "internet_ip": {"ok": True},
                 "dns": {"ok": True}, "http": {"state": "full"}, "server": {"state": "ok"}},
    }
    base.update(over)
    return base


def codes(r):
    return [f["code"] for f in diag.build_findings(r)]


def test_healthy_report_has_no_findings():
    assert codes(report()) == []


def test_dns_only_failure_is_pinpointed():
    r = report()
    r["path"]["dns"] = {"ok": False}
    assert codes(r) == ["dns_failing"]


def test_gateway_down_is_the_only_path_finding():
    r = report()
    r["path"]["gateway"]["ok"] = False
    r["path"]["internet_ip"] = {"ok": False}
    assert codes(r) == ["gateway_unreachable"]


def test_portal_suppresses_path_noise_and_failed_attempt_is_surfaced():
    r = report()
    r["status"].connectivity = "portal"
    r["path"]["internet_ip"] = {"ok": False}
    r["attempts"] = [{"ssid": "x", "ok": False, "reason": "wrong_password", "security_kind": "wpa3", "raw": "r"}]
    assert sorted(codes(r)) == ["captive_portal", "last_connect_failed"]


def test_errors_sort_before_warnings():
    r = report()
    r["system"]["regulatory_country"] = "00"
    r["radio"] = {"present": False}
    assert codes(r)[0] == "no_wifi_device"
