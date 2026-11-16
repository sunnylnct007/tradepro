"""16 Nov 2026: a computed options board was lost to twelve minutes of failed
DNS lookups. The API host's address is remembered while DNS works and used
when a later lookup fails."""
import socket

import pytest

from tradepro_strategies import api_address as A

HOST, ADDR = "tradepro.example.test", "203.0.113.7"


@pytest.fixture
def dns(monkeypatch):
    state = {"down": False, "asked": []}

    def fake(host, *a, **k):
        state["asked"].append(host)
        if host == ADDR:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ADDR, 443))]
        if host == HOST and not state["down"]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ADDR, 443))]
        raise socket.gaierror(8, "nodename nor servname provided")

    monkeypatch.setattr(A, "_real_getaddrinfo", fake)
    monkeypatch.setattr(A, "_known", {})
    monkeypatch.setattr(A, "_dns_down", set())
    monkeypatch.setattr(A, "_warned", set())
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)  # restored after
    return state


def test_a_failed_lookup_connects_to_the_remembered_address(dns):
    assert A.remember(f"https://{HOST}") == ADDR
    dns["down"] = True
    assert socket.getaddrinfo(HOST, 443)[0][4][0] == ADDR


def test_after_one_failure_it_stops_asking_the_broken_dns(dns):
    A.remember(f"https://{HOST}")
    dns["down"] = True
    socket.getaddrinfo(HOST, 443)
    dns["asked"].clear()
    socket.getaddrinfo(HOST, 443)
    assert dns["asked"] == [ADDR]          # a hanging lookup is not retried


def test_other_hosts_are_untouched(dns):
    A.remember(f"https://{HOST}")
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("elsewhere.example.test", 443)


def test_nothing_is_installed_when_dns_is_already_down(dns):
    dns["down"] = True
    assert A.remember(f"https://{HOST}") is None
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo(HOST, 443)


def test_an_address_base_url_needs_nothing(dns):
    assert A.remember(f"http://{ADDR}") is None
