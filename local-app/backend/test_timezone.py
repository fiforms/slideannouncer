"""The timezone chosen on the device: validated, stored under /data, preferred to
the boot yaml, and re-applied at boot by firstboot.py (which carries its own copy
of the precedence rule)."""
import asyncio
import importlib.machinery
import importlib.util
import os

import pytest
from fastapi import HTTPException

import main
import pairing
import system_control

run = asyncio.run
ROOT = os.environ.get("PLATFORM_ROOT")


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(pairing, "TIMEZONE_FILE", tmp_path / "status" / "timezone")
    monkeypatch.setattr(pairing, "BOOT_YAML", tmp_path / "slideannouncer.yaml")
    return tmp_path


@pytest.mark.parametrize("name", ["America/New_York", "Europe/London", "UTC", "America/Argentina/Buenos_Aires", " Asia/Tokyo "])
def test_validate_accepts_real_zones(name):
    assert pairing.validate_timezone(name) == name.strip()


@pytest.mark.parametrize("name", ["", "  ", "Mars/Olympus_Mons", "America", "../etc/passwd", "America/../../etc/passwd", "UTC; reboot", "America/New York", "a" * 300])
def test_validate_rejects_anything_else(name):
    with pytest.raises(pairing.TimezoneError):
        pairing.validate_timezone(name)


def test_the_picker_list_is_places_plus_utc():
    zones = pairing.list_timezones()
    assert "America/New_York" in zones and "UTC" in zones
    assert not any(z.startswith(("Etc/", "US/", "posix/", "right/")) for z in zones)
    assert zones == sorted(zones)


def test_device_choice_beats_the_boot_yaml_beats_nothing(files):
    assert pairing.read_timezone() is None
    files.joinpath("slideannouncer.yaml").write_text("timezone: America/Chicago\n")
    assert pairing.read_timezone() == ("America/Chicago", "boot")
    pairing.write_timezone("Europe/Paris")
    assert pairing.read_timezone() == ("Europe/Paris", "device")
    assert pairing.read_boot_timezone() == "America/Chicago"  # still the default underneath


def test_set_saves_then_applies(files, monkeypatch):
    applied = []

    async def fake_apply(name):
        applied.append(name)

    monkeypatch.setattr(system_control, "apply_timezone", fake_apply)
    assert run(main.timezone_set(main.TimezoneRequest(value="Asia/Tokyo"))) == {"ok": True, "timezone": "Asia/Tokyo", "source": "device"}
    assert applied == ["Asia/Tokyo"]
    assert pairing.read_device_timezone() == "Asia/Tokyo"


def test_set_rejects_a_bad_name_without_saving_or_applying(files, monkeypatch):
    async def boom(name):
        raise AssertionError("must not apply")

    monkeypatch.setattr(system_control, "apply_timezone", boom)
    with pytest.raises(HTTPException) as err:
        run(main.timezone_set(main.TimezoneRequest(value="Nowhere/Land")))
    assert err.value.status_code == 422
    assert pairing.read_device_timezone() is None


def test_a_failed_apply_still_keeps_the_choice_for_next_boot(files, monkeypatch):
    async def fail(name):
        raise system_control.SystemCommandError("access denied")

    monkeypatch.setattr(system_control, "apply_timezone", fail)
    with pytest.raises(HTTPException) as err:
        run(main.timezone_set(main.TimezoneRequest(value="Asia/Tokyo")))
    assert err.value.status_code == 502 and "restart" in err.value.detail
    assert pairing.read_device_timezone() == "Asia/Tokyo"


def test_status_reports_the_running_zone_and_where_it_came_from(files, monkeypatch):
    async def running():
        return "America/New_York"

    monkeypatch.setattr(system_control, "current_timezone", running)
    status = run(main.timezone_status())
    assert (status["timezone"], status["source"]) == ("America/New_York", "image")
    assert "America/New_York" in status["timezones"]
    pairing.write_timezone("Asia/Tokyo")
    assert run(main.timezone_status())["source"] == "device"


@pytest.fixture
def firstboot(files, monkeypatch):
    if not ROOT:
        pytest.skip("PLATFORM_ROOT not set")
    loader = importlib.machinery.SourceFileLoader("firstboot_under_test", f"{ROOT}/provisioning/firstboot.py")
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    monkeypatch.setattr(module, "TIMEZONE_STATUS_FILE", files / "status" / "timezone")
    monkeypatch.setattr(module, "BOOT_YAML", files / "slideannouncer.yaml")
    zones = files / "zoneinfo"
    for name in ("America/Chicago", "Europe/Paris"):
        (zones / name).parent.mkdir(parents=True, exist_ok=True)
        (zones / name).write_text("")
    monkeypatch.setattr(module, "ZONEINFO_DIR", zones)
    return module


def test_firstboot_follows_the_same_precedence(firstboot, files):
    assert firstboot.desired_timezone() is None
    files.joinpath("slideannouncer.yaml").write_text("timezone: America/Chicago\n")
    assert firstboot.desired_timezone() == "America/Chicago"
    pairing.write_timezone("Europe/Paris")
    assert firstboot.desired_timezone() == "Europe/Paris"


def test_firstboot_ignores_a_name_that_is_not_a_zone_file(firstboot, files):
    files.joinpath("slideannouncer.yaml").write_text("timezone: America/Chicago\n")
    pairing.write_timezone("../../etc/passwd")
    assert firstboot.desired_timezone() == "America/Chicago"  # the bad device value is skipped, not obeyed
    pairing.write_timezone("Nowhere/Land")
    assert firstboot.desired_timezone() == "America/Chicago"
