"""Emulator backends, selected by ``config["emulator"]["type"]``.

Before this package that key was decorative: ``configs/config-5.1.yaml`` said
``type: mgba`` and nothing read it. :func:`make_emulator` is the one place that
turns it into a choice.

Adding a backend is three steps: write the class against
:class:`src.emulator.backends.base.EmulatorBackend`, add one line to
:data:`_BACKENDS`, and give its config block a ``type``.
"""

from __future__ import annotations

import importlib
from typing import Any

from src.emulator.backends.base import EmulatorBackend, TracingEmulatorBackend

# type name -> "module path:class name". Imported lazily so a backend whose
# dependencies are not installed cannot break the backends nobody asked for.
_BACKENDS: dict[str, str] = {
    "mgba": "src.emulator.backends.mgba:EmulatorClient",
    "skyemu": "src.emulator.backends.skyemu:SkyEmuClient",
}

# What ``type:`` means when the config does not say. Every config in configs/
# spells it out; this keeps hand-written test configs (and scripts/) working.
DEFAULT_BACKEND = "mgba"


def known_backends() -> list[str]:
    """The backend type names :func:`make_emulator` accepts, sorted."""
    return sorted(_BACKENDS)


def resolve_backend(backend_type: str) -> type:
    """The class registered for ``backend_type``.

    Raises :class:`ValueError` naming every known type. An unknown type must
    never fall through to mgba: a run started with a typo in ``type:`` would
    then quietly produce an mGBA run wearing the wrong label, which is worse
    than not starting.
    """
    try:
        target = _BACKENDS[backend_type]
    except KeyError:
        raise ValueError(
            f"Unknown emulator type {backend_type!r}. "
            f"Known types: {', '.join(known_backends())}. "
            "Set emulator.type in the config to one of these."
        ) from None

    module_path, _, class_name = target.partition(":")
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def make_emulator(config: dict[str, Any]) -> EmulatorBackend:
    """Build the backend that ``config["emulator"]["type"]`` asks for.

    Takes the whole config, not the emulator block: every backend so far reads
    ``screenshot``, ``valid_inputs`` and ``screen_stability`` as well.
    """
    emu_config = config.get("emulator") or {}
    backend_type = emu_config.get("type", DEFAULT_BACKEND)
    return resolve_backend(backend_type)(config)


__all__ = [
    "DEFAULT_BACKEND",
    "EmulatorBackend",
    "TracingEmulatorBackend",
    "known_backends",
    "make_emulator",
    "resolve_backend",
]
