"""Version comparison on the device: local-app-seed.py (boot) and
updater/local_app_updater.py (OTA) both order app releases by the PAIR
<platform X.Y.Z>_<product X.Y.Z>, a missing product part counting as 0.0.0.
They keep separate copies of version_core() on purpose, so this checks both
and that they agree. The scripts live outside the backend, so they are found
through PLATFORM_ROOT (exported by run-tests.sh).
"""
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = os.environ.get("PLATFORM_ROOT")
pytestmark = pytest.mark.skipif(not ROOT, reason="PLATFORM_ROOT not set (run through local-app/run-tests.sh)")


def load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, Path(ROOT) / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", params=["seed", "updater"])
def version_core(request):
    if request.param == "seed":
        return load("local_app_seed", "system/scripts/local-app-seed.py").version_core
    return load("local_app_updater", "updater/local_app_updater.py").version_core


def test_a_pair_parses_to_platform_then_product(version_core):
    assert version_core("0.4.0_0.1.1") == (0, 4, 0, 0, 1, 1)


def test_whatever_follows_the_pair_is_ignored(version_core):
    assert version_core("0.4.0_0.1.1-8066cfc-dirty-slideannouncer-e4b6392") == (0, 4, 0, 0, 1, 1)


def test_a_plain_platform_version_has_product_zero(version_core):
    # What devices carry from before products had their own version.
    assert version_core("0.4.0") == (0, 4, 0, 0, 0, 0)
    assert version_core("0.4.0-8066cfc-slideannouncer-b0.1.0-f0.1.0-8f51f4b") == (0, 4, 0, 0, 0, 0)


@pytest.mark.parametrize("older, newer", [
    ("0.4.0_0.1.0", "0.4.0_0.1.1"),     # product-only bump is an update
    ("0.4.0_0.1.9", "0.4.1_0.1.0"),     # platform wins over product
    ("0.4.0", "0.4.0_0.1.0"),           # legacy -> first pair-versioned release
    ("0.4.0_0.1.1-aaaa", "0.4.0_0.2.0-bbbb"),
    ("0.4.9_0.0.1", "0.4.10_0.0.1"),    # numeric, not lexical
])
def test_ordering(version_core, older, newer):
    assert version_core(older) < version_core(newer)


def test_same_pair_with_a_different_suffix_is_not_newer(version_core):
    # A rebuild of the same versions must not re-seed or re-download.
    assert version_core("0.4.0_0.1.1-aaaa") == version_core("0.4.0_0.1.1-bbbb-dirty")


@pytest.mark.parametrize("junk", ["dev", "", "v0.4.0", "0.4_0.1.1"])
def test_unparseable_versions_are_none(version_core, junk):
    assert version_core(junk) is None
