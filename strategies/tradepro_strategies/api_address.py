"""Reach the API when the Mac's DNS drops out.

THE FAILURE (20 Oct, 23 Oct, 16 Nov 2026). The Mac's name servers stop
answering for minutes at a time. A lane that finished its work then cannot
look up `tradepro.showsoldprice.com`, every publish attempt fails, and the desk
keeps the previous run's board. On 16 Nov the options screen computed 100 rows
(38 eligible), tried ten times over twelve minutes, and lost the day; lanes
posting to the server's address directly carried on through the same outage.

THE FIX. When a lane loads its API base URL, the host's address is looked up
once and remembered. If a later lookup of THAT host fails (or a previous one
did), the connection goes to the remembered address. Only the address lookup
changes: TLS still sends the host name and verifies the certificate against
it, so this is the same HTTPS connection to the same server, never a downgrade.

Never silent: the first time the fallback is used it logs a WARNING naming the
host and the address. Never permanent: nothing is written to disk; a fresh
process looks the name up afresh.
"""
from __future__ import annotations

import logging
import socket
import threading
from urllib.parse import urlparse

log = logging.getLogger("tradepro.api_address")

_lock = threading.Lock()
_known: dict[str, str] = {}          # host -> last good address
_dns_down: set[str] = set()          # hosts whose lookup has failed this process
_warned: set[str] = set()
_real_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, *args, **kwargs):
    h = host.lower() if isinstance(host, str) else host
    addr = _known.get(h) if isinstance(h, str) else None
    if addr is None:
        return _real_getaddrinfo(host, *args, **kwargs)
    if h not in _dns_down:
        try:
            return _real_getaddrinfo(host, *args, **kwargs)
        except socket.gaierror:
            _dns_down.add(h)
    if h not in _warned:
        _warned.add(h)
        log.warning("DNS lookup of %s failed — connecting to its last known "
                    "address %s (TLS still verifies %s)", h, addr, h)
    return _real_getaddrinfo(addr, *args, **kwargs)


def remember(base_url: str) -> str | None:
    """Look up the API host now, while DNS works, and install the fallback.
    Returns the address remembered, or None (no host, or lookup failed now —
    then behaviour is exactly as before)."""
    host = (urlparse(base_url).hostname or "").lower()
    if not host:
        return None
    try:
        socket.inet_aton(host)
        return None                   # already an address: nothing to look up
    except OSError:
        pass
    try:
        infos = _real_getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        addr = next(i[4][0] for i in infos if i[0] == socket.AF_INET)
    except (socket.gaierror, StopIteration, OSError):
        return None
    with _lock:
        _known[host] = addr
        if socket.getaddrinfo is not _getaddrinfo:
            socket.getaddrinfo = _getaddrinfo
    return addr
