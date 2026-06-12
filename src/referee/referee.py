"""Deterministic, observe-only Referee (Phase 4).

The Referee reads FireRed (BPRE-US) game memory out-of-band — the player agent
is provably blind to it (module boundary: nothing in ``src/agent/agent.py``
imports this) — and latches checkpoint first-seen turns.

By default the Referee is OBSERVE-ONLY: it stamps + emits events + persists
state but never terminates a run (calibration runs leave ``enforce=False``).
With ``enforce=True`` (Phase 5) it additionally evaluates deadline gates: a
checkpoint with an int ``deadline_turn`` still unstamped once ``turn_number >=
deadline_turn`` latches ``termination_reason = "missed_gate:<id>"`` (the first
missed gate in ladder order) and signals the turn loop to stop the run cleanly.
The deadline is checked AFTER each poll's stamping, so a gate met exactly on
its deadline turn — or reached early, out of ladder order — is pre-satisfied
and never terminates.

Memory layout (verified addresses, see local/plan-referee-benchmark.md):
  - ``gSaveBlock1Ptr`` @ 0x03005008, ``gSaveBlock2Ptr`` @ 0x0300500C — IWRAM
    pointers, u32 little-endian, dereferenced fresh EVERY poll (DMA shuffle
    relocates the blocks, so a cached pointer goes stale).
  - SaveBlock1 offsets: player x +0x0000 / y +0x0002 (s16); map_group +0x0004
    (u8) / map_num +0x0005 (u8); flags bitfield +0x0EE0 (0x120 bytes); vars
    array +0x1000 (u16 each, addr(var_id) = SB1 + 0x1000 + (var_id-0x4000)*2).
  - Party count @ 0x02024029 (u8, fixed EWRAM, no DMA).

All game knowledge lives here in Python; the emulator/Lua bridge stays dumb
(``read_memory(addr, length) -> bytes``).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Optional

from src.referee.checkpoints import Checkpoint

# --- Verified address constants (FireRed BPRE-US v1.0 == v1.1) -----------------
GSAVEBLOCK1_PTR = 0x03005008
GSAVEBLOCK2_PTR = 0x0300500C  # noqa: F841 (read for tear/diagnostics symmetry)

SB1_PLAYER_X = 0x0000  # s16
SB1_PLAYER_Y = 0x0002  # s16
SB1_MAP_GROUP = 0x0004  # u8
SB1_MAP_NUM = 0x0005  # u8
SB1_FLAGS = 0x0EE0  # 0x120-byte bitfield
SB1_FLAGS_LEN = 0x120
SB1_VARS = 0x1000  # 256 x u16, ids 0x4000..0x40FF

VAR_BASE_ID = 0x4000

PLAYER_PARTY_COUNT = 0x02024029  # u8, fixed EWRAM (no DMA)

# EWRAM sanity band. The plan's tight check is 0x02024000..0x0202A000 (where the
# SaveBlocks normally land); we accept the broader EWRAM band 0x02000000..
# 0x02040000 as a guard against false negatives if a future revision/DMA state
# parks a block just outside the tight window. A pointer outside the broad band
# (e.g. 0x00000000 at the title screen / not in-game) makes the poll a no-op.
EWRAM_BROAD_LO = 0x02000000
EWRAM_BROAD_HI = 0x02040000

# We must read enough of SaveBlock1 in one block to cover map + flags + the var
# we care about. The vars array starts at +0x1000; ids run to 0x40FF, so the
# array is 256*2 = 0x200 bytes. Read through the end of the vars array.
_SB1_READ_LEN = SB1_VARS + 256 * 2  # 0x1200


class Referee:
    """Observe-only checkpoint latch driven by out-of-band memory reads.

    Construct with the loaded checkpoint ladder, an emulator-like object
    exposing ``read_memory(addr, length) -> bytes``, a logger exposing
    ``log_event(event_type, data)``, and the run directory (for state
    persistence so ``--continue`` resumes the latch).
    """

    def __init__(
        self,
        checkpoints: list[Checkpoint],
        emulator: Any,
        logger: Any,
        run_dir: Any,
        enforce: bool = False,
    ) -> None:
        self.checkpoints: list[Checkpoint] = list(checkpoints)
        self._by_id: dict[str, Checkpoint] = {cp.id: cp for cp in self.checkpoints}
        self.emulator = emulator
        self.logger = logger
        self.run_dir = Path(run_dir)
        self._state_path = self.run_dir / "referee_state.json"

        # Gate enforcement (Phase 5). When False the Referee is observe-only,
        # behaving EXACTLY as Phase 4 (used by calibration runs). When True a
        # checkpoint with an int deadline_turn that is still unstamped once its
        # deadline turn is reached terminates the run.
        self.enforce = enforce

        # Set to "missed_gate:<id>" the first time a gate is missed under
        # enforcement; otherwise None. Once set, never cleared.
        self.terminated_reason: Optional[str] = None

        # The latch: checkpoint id -> first-seen turn. NEVER un-stamped.
        self.stamps: dict[str, int] = {}
        self._load_state()

    # --- persistence ----------------------------------------------------------

    def _load_state(self) -> None:
        """Restore the latch from ``referee_state.json`` if present (--continue)."""
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path) as f:
                data = json.load(f)
            raw = data.get("stamps", {})
            # Keep only ids still in the ladder; coerce turns to int.
            for cp_id, turn in raw.items():
                if cp_id in self._by_id:
                    self.stamps[cp_id] = int(turn)
        except Exception:
            # Corrupt/partial state must never crash the run — start fresh.
            self.stamps = {}

    def _persist_state(self) -> None:
        """Write the latch to ``referee_state.json`` (best-effort)."""
        try:
            self.run_dir.mkdir(parents=True, exist_ok=True)
            with open(self._state_path, "w") as f:
                json.dump({"stamps": self.stamps}, f, indent=2)
        except Exception:
            pass  # persistence failure must never take down a run

    # --- read layer -----------------------------------------------------------

    def _read_u32(self, addr: int) -> int:
        """Read a little-endian u32 from a GBA bus address."""
        return struct.unpack("<I", self.emulator.read_memory(addr, 4))[0]

    def _read_saveblock1_ptr(self) -> int:
        """Dereference gSaveBlock1Ptr fresh (DMA shuffle — never cache)."""
        return self._read_u32(GSAVEBLOCK1_PTR)

    @staticmethod
    def _in_ewram(ptr: int) -> bool:
        return EWRAM_BROAD_LO <= ptr < EWRAM_BROAD_HI

    def _read_memory_snapshot(self) -> Optional["_MemorySnapshot"]:
        """Read the SaveBlock1 data block + party count for one poll.

        Returns ``None`` (poll is a no-op) when the pointer is out of the EWRAM
        band (title screen / not in-game) or a torn read can't be stabilised.
        Implements the tear guard: re-read the SB1 pointer after the data read;
        if it moved (DMA relocated the block mid-read), retry once, then give up
        gracefully for this poll.
        """
        for _attempt in range(2):
            sb1_ptr = self._read_saveblock1_ptr()
            if not self._in_ewram(sb1_ptr):
                return None  # not in-game — no-op, no stamp, no crash

            block = self.emulator.read_memory(sb1_ptr, _SB1_READ_LEN)

            # Tear guard: did the block relocate while we were reading it?
            sb1_ptr_after = self._read_saveblock1_ptr()
            if sb1_ptr_after != sb1_ptr:
                continue  # torn read — retry once

            party_count = self.emulator.read_memory(PLAYER_PARTY_COUNT, 1)[0]
            return _MemorySnapshot(block, party_count)

        # Pointer kept moving across both attempts — give up for this poll.
        return None

    # --- poll -----------------------------------------------------------------

    def poll(self, turn_number: int) -> bool:
        """Evaluate every not-yet-stamped checkpoint for this turn.

        On the first satisfaction of a checkpoint, record its first-seen turn,
        emit a ``referee_checkpoint`` event, and persist the latch. A stamp is
        NEVER cleared (backtracking must not un-stamp).

        Returns ``True`` if the run should terminate after this poll (a gate was
        missed under enforcement), else ``False``. The return value is advisory:
        ``poll`` never raises or kills the process itself — the turn loop reads
        the flag (or ``should_terminate()``) and stops the run cleanly.
        """
        snap = self._read_memory_snapshot()
        if snap is None:
            # Out-of-range pointer or unrecoverable torn read. We still surface
            # any termination latched on a prior poll so a transient bad read
            # near a deadline doesn't un-stick a missed gate.
            return self.should_terminate()

        newly_stamped = False
        for cp in self.checkpoints:
            if cp.id in self.stamps:
                continue  # latched — never re-evaluate, never un-stamp
            if self._satisfied(cp, snap):
                self.stamps[cp.id] = turn_number
                newly_stamped = True
                self.logger.log_event(
                    "referee_checkpoint",
                    {
                        "id": cp.id,
                        "name": cp.name,
                        "type": cp.type,
                        "turn": turn_number,
                    },
                )

        if newly_stamped:
            self._persist_state()

        # Deadline check (Phase 5). Runs AFTER this poll's stamping so a gate met
        # exactly on its deadline turn counts as satisfied and never terminates.
        if self.enforce:
            self._check_deadlines(turn_number)

        return self.should_terminate()

    def _check_deadlines(self, turn_number: int) -> None:
        """Latch ``terminated_reason`` if a deadline gate is missed.

        Evaluated as ``turn_number >= deadline_turn`` with the gate still
        unstamped after this poll's stamping. The FIRST (lowest ladder-order)
        missed gate wins. Idempotent: once latched, never re-evaluated.
        """
        if self.terminated_reason is not None:
            return
        for cp in self.checkpoints:
            if cp.deadline_turn is None:
                continue  # observed-only gate — never terminates
            if cp.id in self.stamps:
                continue  # satisfied (possibly out of order / early) — pre-met
            if turn_number >= cp.deadline_turn:
                self.terminated_reason = f"missed_gate:{cp.id}"
                self.logger.log_event(
                    "referee_gate_missed",
                    {
                        "id": cp.id,
                        "name": cp.name,
                        "type": cp.type,
                        "deadline_turn": cp.deadline_turn,
                        "turn": turn_number,
                    },
                )
                return  # first missed gate decides

    def should_terminate(self) -> bool:
        """True iff enforcement has latched a missed-gate termination."""
        return self.terminated_reason is not None

    @property
    def termination_reason(self) -> Optional[str]:
        """``"missed_gate:<id>"`` once a gate is missed under enforcement, else None."""
        return self.terminated_reason

    def _satisfied(self, cp: Checkpoint, snap: "_MemorySnapshot") -> bool:
        """Detector dispatch for a single checkpoint type."""
        sig = cp.signature
        if cp.type == "map":
            return (
                snap.map_group == sig["map_group"]
                and snap.map_num == sig["map_num"]
            )
        if cp.type == "flag":
            return snap.flag_set(sig["flag_id"])
        if cp.type == "var":
            return snap.var_value(sig["var_id"]) >= sig["min_value"]
        if cp.type == "party":
            return snap.party_count >= sig["min_count"]
        return False  # unknown types are rejected at load time

    # --- scorecard ------------------------------------------------------------

    def scorecard(self) -> dict:
        """Per-checkpoint first-seen turn, deepest stamped rung, termination.

        ``furthest`` is the id of the deepest stamped checkpoint in LADDER
        ORDER (not first-seen order) — stamps can land out of order.
        ``termination_reason`` is ``"missed_gate:<id>"`` when enforcement
        terminated the run on a missed deadline, else ``None`` (observe-only
        runs, or enforced runs that hit no missed gate).
        """
        per_checkpoint: dict[str, Optional[int]] = {
            cp.id: self.stamps.get(cp.id) for cp in self.checkpoints
        }
        furthest: Optional[str] = None
        for cp in self.checkpoints:
            if cp.id in self.stamps:
                furthest = cp.id
        return {
            "checkpoints": per_checkpoint,
            "furthest": furthest,
            "termination_reason": self.terminated_reason,
        }


class _MemorySnapshot:
    """Decoded view of one poll's SaveBlock1 block + party count.

    Holds the raw 0x1200-byte SaveBlock1 read (offset 0) plus the party count;
    decodes fields lazily on access. All multi-byte fields are little-endian.
    """

    __slots__ = ("_block", "party_count")

    def __init__(self, block: bytes, party_count: int) -> None:
        self._block = block
        self.party_count = party_count

    @property
    def map_group(self) -> int:
        return self._block[SB1_MAP_GROUP]

    @property
    def map_num(self) -> int:
        return self._block[SB1_MAP_NUM]

    @property
    def player_x(self) -> int:
        return struct.unpack_from("<h", self._block, SB1_PLAYER_X)[0]

    @property
    def player_y(self) -> int:
        return struct.unpack_from("<h", self._block, SB1_PLAYER_Y)[0]

    def flag_set(self, flag_id: int) -> bool:
        """True iff story-flag ``flag_id``'s bit is set in the bitfield."""
        byte_index = SB1_FLAGS + (flag_id >> 3)
        bit = flag_id & 7
        return bool((self._block[byte_index] >> bit) & 1)

    def var_value(self, var_id: int) -> int:
        """Read u16 var ``var_id`` (0x4000..0x40FF) from the vars array."""
        offset = SB1_VARS + (var_id - VAR_BASE_ID) * 2
        return struct.unpack_from("<H", self._block, offset)[0]
