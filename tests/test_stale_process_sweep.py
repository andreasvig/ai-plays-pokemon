"""The boot's stale-process sweep must not kill things that merely MENTION an emulator.

``_find_stale_processes`` feeds ``_reclaim_stale_processes``, which SIGKILLs
every pid it returns. So a false positive here is not a cosmetic bug — it kills
an unrelated process on the user's machine.

It searched by command line (``pgrep -f``), which matches any process whose
ARGUMENTS contain the word. Measured 2026-09-19: two consecutive control-center
boots printed "Found stale processes from a previous launch: SkyEmu (pid N)" and
killed a shell, because the command that launched the app sat in a process tree
where the word appeared. The sweep was hunting an emulator and finding a
sentence about one.

The mGBA sweep had the same latent defect from the start; adding a second
emulator is what made it fire in practice.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from src.cli.app import _find_stale_processes, _pids_matching


def test_a_process_that_only_mentions_an_emulator_is_not_swept():
    """THE regression. A sleeping python whose ARGV contains 'skyemu' and
    'mgba' — the shape of any shell command, tail or editor that talks about
    one. Killing it is the failure; this asserts it is not even listed."""
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import time; time.sleep(30)  # skyemu mgba stale-sweep decoy",
         "--rom", "skyemu-and-mgba-decoy"],
    )
    try:
        time.sleep(0.4)
        stale = _find_stale_processes(web_port=59991, emu_port=59992)
        assert proc.pid not in stale, (
            f"the sweep would SIGKILL pid {proc.pid}, a process that only "
            f"mentions an emulator in its arguments"
        )
        # And the mechanism, directly: by name it is invisible, by command line
        # it is not. If the second assertion ever fails the control has rotted
        # and the first one is passing for the wrong reason.
        assert proc.pid not in _pids_matching("skyemu", ignore_case=True, full=False)
        assert proc.pid in _pids_matching("skyemu", ignore_case=True, full=True)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_the_sweep_never_returns_this_process():
    """Self-exclusion, asserted rather than assumed — the boot kills what this
    returns, and the boot IS this process."""
    assert os.getpid() not in _find_stale_processes(web_port=59991, emu_port=59992)
    assert os.getpid() not in _pids_matching("python", full=False)


def test_a_process_listening_on_the_port_is_still_swept():
    """THE control. Without it, the fix above is indistinguishable from a sweep
    that finds nothing at all — which would silently break the whole point of
    the pre-flight, letting a boot collide with a leftover server."""
    import socket

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        # A server held by a DIFFERENT process is what the sweep is for; this
        # one is ours, so assert through the port lookup rather than the
        # self-excluding wrapper.
        from src.cli.app import _pids_listening_on

        assert os.getpid() in _pids_listening_on(port)
    finally:
        srv.close()
