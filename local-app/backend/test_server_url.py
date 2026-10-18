"""The server address chosen on the device (docs/SERVER_URL.md): how it is
normalised, stored, and preferred over the boot yaml — in the backend and in the
standalone update CLI that carries its own copy of the rule."""
import asyncio
import importlib.machinery
import importlib.util
import os

import pytest
from fastapi import HTTPException

import main
import pairing
import product
import server_check

run = asyncio.run


@pytest.fixture
def files(tmp_path, monkeypatch):
    """Point every file the feature touches at a temp dir."""
    monkeypatch.setattr(pairing, "SERVER_URL_FILE", tmp_path / "status" / "server-url")
    monkeypatch.setattr(pairing, "BOOT_YAML", tmp_path / "slideannouncer.yaml")
    monkeypatch.setattr(pairing, "SETUP_COMPLETE_FILE", tmp_path / "status" / "setup-complete")
    monkeypatch.setattr(pairing, "DEVICE_TOKEN_FILE", tmp_path / "device-token")
    return tmp_path


def confirming_product(monkeypatch, confirm=True):
    monkeypatch.setattr(product, "_loaded", product.Product(name="fake", api_base="/api/devices", confirm_server_url=confirm))


@pytest.mark.parametrize("typed, expected", [
    ("https://relief.example.org", "https://relief.example.org"),
    ("  https://relief.example.org/  ", "https://relief.example.org"),
    ("relief.example.org", "https://relief.example.org"),  # a bare host gets https
    ("http://10.0.0.5:8080", "http://10.0.0.5:8080"),
    ("https://example.org/app/", "https://example.org/app"),
])
def test_normalize_accepts_and_tidies(typed, expected):
    assert pairing.normalize_server_url(typed) == expected


@pytest.mark.parametrize("typed", [
    "", "   ", "ftp://example.org", "https://", "https://user:pw@example.org", "https://example.org/?a=1",
    "https://example.org/#x", "https://example.org:notaport", "javascript:alert(1)",
])
def test_normalize_rejects_what_cannot_be_a_server_address(typed):
    with pytest.raises(pairing.ServerUrlError):
        pairing.normalize_server_url(typed)


def test_confirmed_url_wins_over_the_boot_yaml(files):
    files.joinpath("slideannouncer.yaml").write_text("server_url: https://from-yaml.example\n")
    assert pairing.read_server_url() == "https://from-yaml.example"  # nothing confirmed: the old behaviour
    pairing.write_server_url("https://confirmed.example")
    assert pairing.read_server_url() == "https://confirmed.example"
    assert pairing.read_boot_server_url() == "https://from-yaml.example"  # still the wizard's default


def test_a_confirmed_url_works_without_any_boot_yaml(files):
    pairing.write_server_url("https://confirmed.example")
    assert pairing.read_server_url() == "https://confirmed.example"


def test_without_either_it_still_fails_closed(files):
    with pytest.raises(pairing.PairingError):
        pairing.read_server_url()
    files.joinpath("slideannouncer.yaml").write_text("default_language: en\n")
    with pytest.raises(pairing.PairingError, match="server_url is not set"):
        pairing.read_server_url()


def test_a_blank_or_unreadable_confirmed_file_is_ignored(files):
    (files / "status").mkdir()
    (files / "status" / "server-url").write_text("\n")
    files.joinpath("slideannouncer.yaml").write_text("server_url: https://from-yaml.example\n")
    assert pairing.read_server_url() == "https://from-yaml.example"


def test_written_file_is_world_readable_and_replaced_atomically(files):
    pairing.write_server_url("https://a.example")
    pairing.write_server_url("https://b.example")
    path = pairing.SERVER_URL_FILE
    assert path.read_text() == "https://b.example\n"
    assert path.stat().st_mode & 0o777 == 0o644  # the update CLI and backend run as different users
    assert not path.with_name("server-url.tmp").exists()


def test_it_survives_an_unpair(files):
    pairing.write_server_url("https://confirmed.example")
    assert pairing.SERVER_URL_FILE not in pairing.WIPE_PATHS


# ---- the endpoint

def post(url, verify=True):
    return run(main.set_server_url(main.ServerUrlRequest(url=url, verify=verify)))


def fake_probe(monkeypatch, state="ok", detail=None):
    seen = []

    async def probe(url):
        seen.append(url)
        return {"state": state, "host": "relief.example.org", "detail": detail, "latency_ms": 5}

    monkeypatch.setattr(server_check, "probe", probe)
    return seen


def test_endpoint_verifies_then_saves_the_normalised_url(files, monkeypatch):
    confirming_product(monkeypatch)
    seen = fake_probe(monkeypatch)
    assert post(" relief.example.org/ ") == {"server_url": "https://relief.example.org", "check": "ok"}
    assert seen == ["https://relief.example.org"]
    assert pairing.read_confirmed_server_url() == "https://relief.example.org"


def test_an_unreachable_server_is_refused_with_the_reason_and_nothing_is_saved(files, monkeypatch):
    confirming_product(monkeypatch)
    fake_probe(monkeypatch, state="unreachable", detail="timed out")
    with pytest.raises(HTTPException) as err:
        post("https://relief.example.org")
    assert err.value.status_code == 422 and "timed out" in err.value.detail
    assert pairing.read_confirmed_server_url() is None


def test_use_it_anyway_skips_the_check(files, monkeypatch):
    confirming_product(monkeypatch)
    seen = fake_probe(monkeypatch, state="unreachable")
    assert post("https://relief.example.org", verify=False)["check"] == "unchecked"
    assert seen == [] and pairing.read_confirmed_server_url() == "https://relief.example.org"


def test_a_bad_address_is_a_422(files, monkeypatch):
    confirming_product(monkeypatch)
    with pytest.raises(HTTPException) as err:
        post("ftp://nope")
    assert err.value.status_code == 422


def test_it_can_be_corrected_during_setup_but_not_after(files, monkeypatch):
    confirming_product(monkeypatch)
    fake_probe(monkeypatch)
    post("https://typo.example.org")
    post("https://right.example.org")  # first-run Back + retype
    assert pairing.read_server_url() == "https://right.example.org"

    pairing.mark_setup_complete()
    with pytest.raises(HTTPException) as err:
        post("https://other.example.org")
    assert err.value.status_code == 409 and "factory reset" in err.value.detail
    assert pairing.read_server_url() == "https://right.example.org"


def test_a_paired_device_cannot_switch_either(files, monkeypatch):
    confirming_product(monkeypatch)
    fake_probe(monkeypatch)
    files.joinpath("device-token").write_text("secret")
    with pytest.raises(HTTPException) as err:
        post("https://other.example.org")
    assert err.value.status_code == 409


def test_products_that_dont_confirm_the_server_are_unaffected(files, monkeypatch):
    confirming_product(monkeypatch, confirm=False)
    with pytest.raises(HTTPException) as err:
        post("https://relief.example.org")
    assert err.value.status_code == 404
    assert pairing.read_confirmed_server_url() is None


def test_status_reports_the_default_and_whether_it_was_confirmed(files, monkeypatch):
    confirming_product(monkeypatch)
    files.joinpath("slideannouncer.yaml").write_text("server_url: https://from-yaml.example\n")
    status = main.local_status()
    assert (status["confirm_server_url"], status["server_url_default"], status["server_url_confirmed"]) == (True, "https://from-yaml.example", False)
    pairing.write_server_url("https://confirmed.example")
    status = main.local_status()
    assert (status["server_url"], status["server_url_confirmed"], status["server_url_locked"]) == ("https://confirmed.example", True, False)


# ---- the update CLI's own copy of the rule

ROOT = os.environ.get("PLATFORM_ROOT")


@pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")
def test_update_cli_prefers_the_confirmed_url_like_the_backend(tmp_path, monkeypatch):
    loader = importlib.machinery.SourceFileLoader("rauc_update_cli", f"{ROOT}/system/scripts/rauc-update.py")
    cli = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(cli)
    yaml_file, confirmed = tmp_path / "slideannouncer.yaml", tmp_path / "server-url"
    yaml_file.write_text("server_url: https://from-yaml.example/\n")
    monkeypatch.setattr(cli, "BOOT_YAML", yaml_file)
    monkeypatch.setattr(cli, "SERVER_URL_FILE", confirmed)

    assert cli.read_server_url() == "https://from-yaml.example"
    confirmed.write_text("https://confirmed.example\n")
    assert cli.read_server_url() == "https://confirmed.example"
    yaml_file.unlink()
    assert cli.read_server_url() == "https://confirmed.example"  # no boot yaml needed any more
    confirmed.unlink()
    with pytest.raises(SystemExit):
        cli.read_server_url()
