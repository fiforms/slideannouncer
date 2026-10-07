"""The seam between this backend's product-neutral core (WiFi/network
settings, pairing, heartbeat, system control, updates) and the one product
built on top of it — see ../../docs/PRODUCTS.md.

A product is a package at `products/<name>/` whose `__init__.py` exposes a
module-level `product = Product(...)`. Which one runs is decided at package
time: package.sh writes the name into the release's `PRODUCT` file, which
`load()` reads (the `KIOSK_PRODUCT` env var overrides it, for local runs
and tests). With neither, get() raises: there is no built-in product.

Core modules must not import a product at module level — products import
core (pairing, system_control, …), so that would be circular. They call
`get()` at the point of use instead, which imports the product on first
call.
"""
import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

PRODUCT_FILE = Path(__file__).resolve().parent.parent / "PRODUCT"


@dataclass
class Product:
    name: str
    # Path prefix, on the management server, of the core device contract
    # (`{api_base}/pair`, `{api_base}/heartbeat`, …) — see
    # docs/DEVICE_CONTRACT.md. A product's own server endpoints live under
    # the same prefix, reached through pairing.api_url().
    api_base: str
    # FastAPI routers mounted on the app, for the product's own
    # `/api/local/<product feature>` endpoints.
    routers: list = field(default_factory=list)
    # Zero-argument coroutine functions started with the backend and
    # cancelled with it (sync loops, stream bridges, print pollers, …).
    # Each must catch its own exceptions — a bug here must never take the
    # backend down.
    background_tasks: list[Callable[[], Awaitable[None]]] = field(default_factory=list)
    # Extra top-level fields merged into GET /api/local/status.
    status_fields: Callable[[], dict] = lambda: {}
    # Product data riding on the core heartbeat: merged into the request
    # body, and handed the full response body after a successful one.
    heartbeat_payload: Callable[[], dict] = lambda: {}
    on_heartbeat_response: Callable[[dict], None] = lambda response: None
    # Product state wiped with the rest of the pairing state on unpair /
    # revocation (a re-pair may attach this device to different data).
    wipe_paths: list[Path] = field(default_factory=list)


_loaded: Product | None = None


def product_name() -> str:
    name = os.environ.get("KIOSK_PRODUCT")
    if not name and PRODUCT_FILE.exists():
        name = PRODUCT_FILE.read_text().strip()
    if not name:
        raise RuntimeError(
            "No product configured: expected a PRODUCT file next to backend/ "
            "(written by package.sh) or the KIOSK_PRODUCT environment variable."
        )
    return name


def get() -> Product:
    global _loaded
    if _loaded is None:
        _loaded = importlib.import_module(f"products.{product_name()}").product
    return _loaded
