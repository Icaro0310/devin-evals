"""Offline-core guard: the core must not open sockets.

Release checklist item: "no-network core test (CI): fails if the core
opens a socket". An autouse fixture monkeypatches ``socket.socket.connect``,
``socket.socket.connect_ex`` and ``socket.create_connection`` to raise
``OfflineCoreError`` for the duration of every test in this file, then the
tests run the repo's core operations end to end. This is a guard, not a
mock: any in-process network access fails the suite.

Intentional online paths are excluded by design: none exist — unit/inject/
fleet are deterministic local fixture generators. Only in-process sockets
are blocked here.

Opt-out: mark a test ``@pytest.mark.network`` to run it without the socket
block (reserved for tests that intentionally exercise the network).

Run with:
``PYTHONPATH=src:../devin-internals-spec/src python -m pytest tests/test_offline_core.py``
(this repo imports ``devin_internals`` from the sibling checkout).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from devin_evals.dream.cli import main  # noqa: E402
from devin_internals.parsers import SessionsStore  # noqa: E402


class OfflineCoreError(RuntimeError):
    """Raised when core code tries to open a network connection."""


def _offline_fail(*args, **kwargs):
    raise OfflineCoreError("core opened a socket during the offline-core test")


@pytest.fixture(autouse=True)
def _block_sockets(request, monkeypatch):
    """Block all outbound sockets; opt out with ``@pytest.mark.network``."""
    if request.node.get_closest_marker("network"):
        return
    monkeypatch.setattr(socket.socket, "connect", _offline_fail)
    monkeypatch.setattr(socket.socket, "connect_ex", _offline_fail)
    monkeypatch.setattr(socket, "create_connection", _offline_fail)


def test_socket_block_is_active():
    """Sanity check: the guard itself raises on any connect attempt."""
    with pytest.raises(OfflineCoreError):
        socket.create_connection(("127.0.0.1", 1), timeout=0.01)
    with pytest.raises(OfflineCoreError):
        socket.socket().connect(("127.0.0.1", 1))


def test_generate_unit_defect_offline(tmp_path):
    """`unit --defect D04` writes a parseable sessions.db — offline."""
    out = tmp_path / "u"
    assert main(["unit", "--out", str(out), "--defect", "D04"]) == 0

    db = out / "d04" / "sessions.db"
    assert db.is_file()
    expected = json.loads((db.parent / "expected.json").read_text())
    assert expected["defect"] == "D04" and expected["expected"]

    with SessionsStore(db) as st:
        assert len(st.sessions()) == 1
        assert st.sessions()[0].id == expected["session_id"]
        assert st.message_nodes()
