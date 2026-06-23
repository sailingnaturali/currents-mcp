"""resolve_local_host: .local mDNS hosts hang httpx async connect on macOS, so
we resolve them to IPv4 up front. (Mirrors signalk-mcp's fix.)"""
import socket
from unittest.mock import patch

from currents_mcp.netutil import resolve_local_host


def _fake_getaddrinfo(ip):
    def _f(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
    return _f


def test_local_host_resolved_to_ipv4():
    with patch("currents_mcp.netutil.socket.getaddrinfo",
               _fake_getaddrinfo("192.168.68.60")):
        assert resolve_local_host("http://naturalaspi.local:3000") == \
            "http://192.168.68.60:3000"


def test_non_local_host_unchanged():
    assert resolve_local_host("http://192.168.68.60:3000") == \
        "http://192.168.68.60:3000"


def test_resolution_failure_falls_back():
    def _boom(*a, **k):
        raise socket.gaierror("no resolve")
    with patch("currents_mcp.netutil.socket.getaddrinfo", _boom):
        assert resolve_local_host("http://naturalaspi.local:3000") == \
            "http://naturalaspi.local:3000"
