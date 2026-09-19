"""Compatibility shim. The real class now lives in
``src/emulator/backends/mgba.py``.

``EmulatorClient`` was this module's only reason to exist until the backend
seam (P0). It moved so a second backend could sit beside it; this file stays
so that ``from src.emulator.emulator import EmulatorClient`` — four test
modules and ``src/core/snapshots.py`` — keeps meaning what it meant.

Prefer, in new code:

* ``from src.emulator import make_emulator`` to *build* one (it honours
  ``config["emulator"]["type"]``; constructing ``EmulatorClient`` directly
  pins you to mGBA),
* ``from src.emulator.backends.mgba import EmulatorClient`` when you
  specifically mean the mGBA client, e.g. a wire-protocol test.

``time`` is re-imported below on purpose and is not dead: ``tests/test_wait_input.py``
does ``monkeypatch.setattr(emu_mod.time, "sleep", ...)`` against this module.
It patches the ``time`` module object itself, which ``backends/mgba.py``
shares, so the stub lands where it needs to — but the attribute has to be
reachable here for the test to find it.
"""

import time  # noqa: F401  (see module docstring)

from src.emulator.backends.mgba import EmulatorClient, ProtocolError

__all__ = ["EmulatorClient", "ProtocolError"]
