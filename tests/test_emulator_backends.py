"""The backend seam: ``config["emulator"]["type"]`` picks the client (P0).

Before the seam the key was decorative — ``configs/config-5.1.yaml`` said
``type: mgba`` and nothing read it. These tests pin the two things that makes
it a seam rather than a rename: an unknown type fails loudly instead of
falling through to mgba, and the shipped backend actually satisfies the
written-down protocol.

Headless: the mGBA client opens no socket until ``start_server``.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.emulator import EmulatorClient, known_backends, make_emulator, resolve_backend
from src.emulator.backends.base import EmulatorBackend, TracingEmulatorBackend
from src.emulator.backends.mgba import EmulatorClient as MgbaClient


def _config(**emulator):
    return {"emulator": {"host": "127.0.0.1", "port": 8888, **emulator}}


def _protocol_members(proto) -> set[str]:
    """The names ``proto`` declares — derived from the Protocol itself, never
    hand-listed, so a member added to the contract cannot slip past this file."""
    names = set(getattr(proto, "__annotations__", {}))
    names |= {n for n, v in vars(proto).items() if not n.startswith("_") and callable(v)}
    return names


# --- selection -----------------------------------------------------------


def test_type_mgba_builds_the_mgba_client():
    assert isinstance(make_emulator(_config(type="mgba")), MgbaClient)


def test_absent_type_defaults_to_mgba():
    """Hand-written configs (scripts/probe_memory.py, several tests) omit it."""
    assert isinstance(make_emulator(_config()), MgbaClient)


@pytest.mark.parametrize("bad", ["skyemu", "mGBA", "", "MGBA", "dolphin"])
def test_unknown_type_raises_and_names_the_known_types(bad):
    """An unknown type must NOT fall through to mgba.

    A typo in ``type:`` would otherwise produce an mGBA run wearing the wrong
    label — a silently mislabelled arm is worse than a run that refuses to
    start. The message has to carry the valid choices, because the person
    reading it is looking at a config, not at this file.
    """
    with pytest.raises(ValueError) as exc:
        make_emulator(_config(type=bad))
    message = str(exc.value)
    assert repr(bad) in message
    for name in known_backends():
        assert name in message


def test_resolve_backend_returns_the_class_not_an_instance():
    assert resolve_backend("mgba") is MgbaClient


# --- the protocol the backends implement ---------------------------------


def test_mgba_backend_satisfies_the_protocol():
    """Every member declared in base.py exists on a built mGBA client.

    ``trace_spec`` and ``facing`` are set in ``__init__``, so this checks an
    instance rather than the class.
    """
    emu = make_emulator(_config(type="mgba"))
    missing = sorted(m for m in _protocol_members(EmulatorBackend) if not hasattr(emu, m))
    assert missing == [], f"mGBA backend is missing: {missing}"
    assert isinstance(emu, EmulatorBackend)


def test_mgba_backend_is_a_tracing_backend():
    """``fetch_trace`` is optional (turn.py:1897 reaches for it with getattr),
    but mGBA has it — the referee's per-input trace depends on it."""
    emu = make_emulator(_config(type="mgba"))
    assert isinstance(emu, TracingEmulatorBackend)
    assert "fetch_trace" in _protocol_members(TracingEmulatorBackend)
    assert "fetch_trace" not in _protocol_members(EmulatorBackend)


# --- the compatibility shim ----------------------------------------------


def test_legacy_import_path_is_the_same_class():
    """``from src.emulator.emulator import EmulatorClient`` still works and is
    not a copy — src/core/snapshots.py and four test modules rely on it."""
    from src.emulator.emulator import EmulatorClient as Legacy, ProtocolError

    assert Legacy is MgbaClient
    assert EmulatorClient is MgbaClient
    assert issubclass(ProtocolError, RuntimeError)
