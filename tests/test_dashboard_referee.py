"""Phase 6 — dashboard referee HUD: config endpoint exposes the gate ladder.

Headless coverage for the server side of the HUD. The collapsed-strip /
expandable-panel browser behaviour is a deferred human gate (needs a live run);
here we only assert that ``GET /runs/{id}/api/config`` serves the ladder when
the session config carries a ``referee`` block, and omits it otherwise.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from src.dashboard.event_bridge import EventBridge
from src.dashboard.screen_stream import ScreenStreamer
from src.dashboard.server import RunSession, app, get_registry

CHECKPOINTS_PATH = "configs/checkpoints-firered-v1.yaml"


def _register_session(run_id: str, config: dict) -> None:
    """Register a minimal RunSession (no started streamer/state) for the route."""
    session = RunSession(
        run_id=run_id,
        label=run_id,
        config=config,
        bridge=EventBridge(),
        streamer=ScreenStreamer(stream_path="/tmp/_test_referee_stream.png"),
        state_manager=None,
        run_dir=Path("/tmp") / run_id,
    )
    get_registry().register(session)


def _cleanup(run_id: str) -> None:
    get_registry().unregister(run_id)


def test_config_includes_referee_ladder():
    run_id = "test-referee-ladder"
    config = {
        "task": {"goal": "play"},
        "referee": {"checkpoints": CHECKPOINTS_PATH, "enforce": True},
    }
    _register_session(run_id, config)
    try:
        client = TestClient(app)
        resp = client.get(f"/runs/{run_id}/api/config")
        assert resp.status_code == 200
        payload = resp.json()

        assert "referee" in payload
        ref = payload["referee"]
        assert ref["enforce"] is True

        ladder = ref["ladder"]
        assert len(ladder) == 13

        ids = [c["id"] for c in ladder]
        assert ids[0] == "left_bedroom"
        # order preserved exactly as authored in the yaml
        assert ids == [
            "left_bedroom",
            "left_house",
            "oaks_lab_entered",
            "starter_chosen",
            "rival1_done",
            "route1_reached",
            "viridian_reached",
            "parcel_delivered",
            "pokedex_received",
            "viridian_forest_reached",
            "pewter_reached",
            "pewter_gym_entered",
            "brock_defeated",
        ]

        # names + deadline_turn carried through
        by_id = {c["id"]: c for c in ladder}
        assert by_id["left_house"]["name"] == "Stepped outside in Pallet Town"
        assert by_id["left_house"]["deadline_turn"] == 50
        # observed-only gate keeps a null deadline
        assert by_id["brock_defeated"]["deadline_turn"] is None
    finally:
        _cleanup(run_id)


def test_config_without_referee_omits_ladder():
    run_id = "test-no-referee"
    config = {"task": {"goal": "play"}, "llm_model": "some-model"}
    _register_session(run_id, config)
    try:
        client = TestClient(app)
        resp = client.get(f"/runs/{run_id}/api/config")
        assert resp.status_code == 200
        payload = resp.json()
        assert payload.get("referee") is None
        assert "ladder" not in payload
    finally:
        _cleanup(run_id)


def test_config_referee_missing_file_omits_ladder():
    """A referee block pointing at a missing file must not 500 the endpoint."""
    run_id = "test-referee-badfile"
    config = {
        "task": {"goal": "play"},
        "referee": {"checkpoints": "configs/does-not-exist.yaml", "enforce": True},
    }
    _register_session(run_id, config)
    try:
        client = TestClient(app)
        resp = client.get(f"/runs/{run_id}/api/config")
        assert resp.status_code == 200
        assert resp.json().get("referee") is None
    finally:
        _cleanup(run_id)
