"""Small helpers for read-only system probes (journal, iw, rfkill, ping, …)
used by network.py's connect-failure reporting and network_diagnostics.py.

Every helper degrades instead of raising: a missing binary, a timeout, or
a permission problem gives `rc=None` / `None`, since both callers are
"gather whatever evidence is available" code, not code that depends on it.
"""
import asyncio
import time
from dataclasses import dataclass


@dataclass
class CmdResult:
    rc: int | None  # None = binary missing or timed out
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0


async def run(*argv: str, timeout: float = 8.0) -> CmdResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError):
        return CmdResult(None, "", f"{argv[0]}: not available")
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return CmdResult(None, "", f"{argv[0]}: timed out")
    return CmdResult(proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace"))


# The user-visible half of a wpa_supplicant/NetworkManager log line is
# everything after "host process[pid]:", so drop the hostname and pid noise.
def _tidy(line: str) -> str:
    head, sep, rest = line.partition(": ")
    if not sep:
        return line
    # "2026-10-07T12:00:00-04:00 host NetworkManager[812]" -> "12:00:00 NetworkManager"
    parts = head.split()
    if len(parts) >= 3:
        stamp = parts[0].split("T")[-1][:8]
        proc = parts[2].split("[")[0]
        return f"{stamp} {proc}: {rest}"
    return line


async def journal(since_epoch: float | None = None, lines: int = 120) -> tuple[list[str] | None, bool]:
    """(log lines, restricted). Lines come from NetworkManager and
    wpa_supplicant — the latter holds the real reason a WiFi join failed
    (WRONG_KEY, ASSOC-REJECT status codes, …), which nmcli collapses into
    one generic message. None = no journal at all; `restricted` means the
    service user isn't in the systemd-journal group, so journalctl only
    shows its own (empty) messages."""
    argv = ["journalctl", "-u", "NetworkManager", "-u", "wpa_supplicant",
            "--no-pager", "-o", "short-iso", "-n", str(lines)]
    argv += ["--since", f"@{int(since_epoch)}"] if since_epoch else ["--since", "-30min"]
    result = await run(*argv, timeout=6)
    if result.rc is None or result.rc != 0:
        return None, False
    restricted = "not seeing messages" in result.stderr
    out = [_tidy(line) for line in result.stdout.splitlines()
           if line and not line.startswith("-- ")]
    return out, restricted


def now() -> float:
    return time.time()
