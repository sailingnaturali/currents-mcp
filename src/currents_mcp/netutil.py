"""Network helpers. ``resolve_local_host`` works around httpx async connect
hanging on macOS ``.local`` mDNS hosts (IPv6 Happy-Eyeballs waits out the full
timeout before IPv4 fallback). Canonical copy lives in signalk-mcp; shared-helper
consolidation is tracked in planning ADR 0004."""
from __future__ import annotations

import socket
from urllib.parse import urlsplit, urlunsplit


def resolve_local_host(base_url: str) -> str:
    """Resolve a ``.local`` host to its IPv4 address; return others unchanged.

    Resolution failures fall back to the original URL so httpx still gets a shot.
    """
    parts = urlsplit(base_url)
    host = parts.hostname
    if not host or not host.endswith(".local"):
        return base_url
    try:
        infos = socket.getaddrinfo(host, parts.port, socket.AF_INET,
                                   socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        return base_url
    if not infos:
        return base_url
    ip = infos[0][4][0]
    netloc = ip if parts.port is None else f"{ip}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query,
                       parts.fragment))
