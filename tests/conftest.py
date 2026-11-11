import socket

import pytest


@pytest.fixture(autouse=True)
def offline_by_default(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return

    def blocked(*args, **kwargs):
        raise AssertionError("Offline tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
