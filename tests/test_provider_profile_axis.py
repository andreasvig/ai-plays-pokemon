"""The provider-profile axis on the QUEUE path, and enqueue-time profile checks.

Until config-5.0 became the standard harness, a provider profile was reachable
only from ``pokemon run --provider-profile`` — which fights the control center
for mGBA — so the documented Gemma A/B could not be queued at all (finding #10
of artifacts/append-standard-frontend-review/plan.md), and the thinking-level
dropdown offered the registry's whole ladder on every config while legality on
the append harness lives in the PROFILE (finding #5).

Four layers, mirroring test_gameplay_style.py / test_max_spend_budget.py:

  * the catalog projection — ``/api/profiles``'s two halves;
  * the API door — what is accepted, rejected, and refused for official;
  * the executor — the profile actually reaching the config, the run NAME
    carrying a named variant, and the official guard firing on a hand-edited
    item;
  * the effort guard — reachable only when the registry/profile subset invariant
    is broken, which is exactly why the invariant is asserted here too.

Discipline (memory ``dont-pin-user-tuned-values-in-tests``): assert STRUCTURE.
No test pins an endpoint tag, a reasoning ladder or a compaction number — those
are Andreas's calibration values and move. Where a specific model is named it is
named for a PROPERTY it has (gemma-4-31b is the only model with variants), and
the property is asserted before it is relied on.
"""

from __future__ import annotations

import types

import pytest
import yaml
from fastapi.testclient import TestClient

from src.app.catalog import (
    APPEND_AGENT_TYPE,
    list_config_facts,
    list_configs,
    list_models,
    list_provider_profiles,
)
from src.app.executor import RunExecutor
from src.app.models import QueuedRun, RunKind
from src.app.queue_manager import QueueManager
from src.config import _load_models_registry, model_thinking_levels
from src.dashboard import server


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def api(tmp_path):
    """A control plane with a real QueueManager and a stub executor.

    Same shape as test_gameplay_style.py's: these tests exercise the API door and
    the catalog, neither of which touches the drain.
    """
    server.configure_control_plane(
        queue_manager=QueueManager(tmp_path / "queue.json"),
        executor=types.SimpleNamespace(runs_root=tmp_path / "runs", last_error=None),
        run_index=types.SimpleNamespace(all=lambda: [], get=lambda rid: None),
    )
    yield TestClient(server.app)
    server._CONTROL["queue"] = None
    server._CONTROL["executor"] = None
    server._CONTROL["index"] = None


@pytest.fixture
def executor(tmp_path):
    """A RunExecutor with the REAL ``prepare_config``.

    Deliberately not the ``fake_prepare_config`` double the rest of
    test_app_executor.py uses: the whole chain under test here lives inside
    ``load_config`` → ``resolve_provider_profile`` → ``prepare_config``'s
    run-name suffix, and a stub of it would assert its own behaviour.
    """
    runs_root = tmp_path / "runs"
    runs_root.mkdir()
    return RunExecutor(
        supervisor=types.SimpleNamespace(
            status=lambda: types.SimpleNamespace(busy=False), handle={}
        ),
        queue_manager=QueueManager(tmp_path / "queue.json"),
        run_index=types.SimpleNamespace(all=lambda: [], get=lambda rid: None),
        runs_root=runs_root,
        saves_dir=tmp_path / "saves",
        run_fn=lambda *a, **k: runs_root / "noop",
    )


def _append_stem() -> str:
    """The newest stem whose ``agent_type`` is append_compact (config-5.0 today)."""
    aware = [f["stem"] for f in list_config_facts() if f["profile_aware"]]
    assert aware, "no profile-aware config on disk — nothing to test against"
    return aware[-1]


def _legacy_stem() -> str:
    """A stem that resolves NO provider profile (config-4.0 / 3.13 today).

    The WITHOUT-the-trigger control for every profile behaviour below. Skips
    rather than passing vacuously if the legacy line is ever removed: a test that
    silently stops exercising the legacy branch is worse than a missing one.
    """
    legacy = [f["stem"] for f in list_config_facts() if not f["profile_aware"]]
    if not legacy:
        pytest.skip("no legacy (non-append) config on disk")
    return legacy[-1]


def _model_with_variants() -> tuple[str, list[str]]:
    """A pickable model that HAS named profile variants, plus their names."""
    for row in list_provider_profiles()["profiles"]:
        if row["variants"]:
            return row["model"], row["variants"]
    pytest.skip("no model in the profile catalog has named variants")


def _model_without_variants() -> str:
    """A pickable model that has NO named variants — the negative control."""
    for row in list_provider_profiles()["profiles"]:
        if not row["variants"]:
            return row["model"]
    pytest.skip("every model has variants — no negative control available")


def _alias(model: str) -> str:
    """``model(default_level)`` for a registry model, or the bare name."""
    entry = _load_models_registry()[model]
    levels = model_thinking_levels(entry)
    return f"{model}({levels[0]})" if levels else model


# ── the catalog projection ────────────────────────────────────────────────────


def test_profiles_route_serves_one_row_per_pickable_model(api):
    """Same membership as /api/models — so a retired entry is absent from both.

    Keyed on the model NAME rather than a count: 21 is a calibration value.
    """
    body = api.get("/api/profiles").json()
    served = {row["model"] for row in body["profiles"]}
    assert served == {row["model"] for row in list_models()}


def test_profiles_route_carries_the_transport_contract(api):
    """Every field the dialog branches on is present on every row."""
    for row in api.get("/api/profiles").json()["profiles"]:
        assert set(row) == {
            "model", "openrouter_id", "unprofiled", "endpoint",
            "reasoning_efforts", "reasoning_default", "final_turn_text_only",
            "cache_mode", "variants",
        }
        assert isinstance(row["reasoning_efforts"], list)
        assert isinstance(row["variants"], list)


def test_profiles_route_lists_config_facts_in_api_configs_order(api):
    """`configs` is the same list /api/configs returns, plus the two facts.

    Order matters: the dialog and ``_validate_config_stem`` both take the LAST
    entry as the default, so a differently-ordered second listing would be a
    second, drifting answer to "which config by default".
    """
    body = api.get("/api/profiles").json()
    assert [c["stem"] for c in body["configs"]] == list_configs()


def test_profile_awareness_is_keyed_on_agent_type_not_on_the_stem(api):
    """`profile_aware` must be the config's own ``agent_type``, never its name.

    Mutation control for the whole feature's branch: if this were derived from
    "starts with config-5" the flag would still be right today and wrong the
    moment a config is renamed or a 6.x lands.
    """
    for row in api.get("/api/profiles").json()["configs"]:
        assert row["profile_aware"] == (row["agent_type"] == APPEND_AGENT_TYPE)
    aware = {c["stem"]: c for c in api.get("/api/profiles").json()["configs"]}
    assert aware[_append_stem()]["profile_aware"] is True
    assert aware[_legacy_stem()]["profile_aware"] is False


def test_profile_awareness_reads_the_file_not_the_filename(tmp_path):
    """The mutation control the route-level test above CANNOT provide.

    On the shipped configs, "agent_type is append_compact" and "the stem starts
    with config-5" pick out exactly the same file, so a stem-keyed
    implementation passes every assertion made against `configs/`. This builds
    the two configs that separate them — a NON-5.x stem running the append agent
    and a 5.x stem running the legacy one — and pins both directions.
    """
    (tmp_path / "config-9.9.yaml").write_text(
        yaml.safe_dump({"player_agent": {
            "agent_type": APPEND_AGENT_TYPE, "compaction": {"every_n_turns": 7}}})
    )
    (tmp_path / "config-5.9.yaml").write_text(
        yaml.safe_dump({"player_agent": {"agent_type": "current"}})
    )
    facts = {f["stem"]: f for f in list_config_facts(configs_dir=tmp_path)}
    assert facts["config-9.9"]["profile_aware"] is True
    assert facts["config-9.9"]["compaction_interval"] == 7
    assert facts["config-5.9"]["profile_aware"] is False
    assert facts["config-5.9"]["compaction_interval"] is None


def test_a_flat_config_is_read_like_a_hoisted_one(tmp_path):
    """``agent_type``/``compaction`` may be authored flat (config-1.x..3.12) or
    inside ``player_agent:`` (4.0+); ``src.config._hoist_player_agent``
    normalises that at load time and this projection must match it, since it
    reads the YAML raw rather than loading it."""
    (tmp_path / "config-1.0.yaml").write_text(
        yaml.safe_dump({"agent_type": APPEND_AGENT_TYPE,
                        "compaction": {"every_n_turns": 3}})
    )
    facts = {f["stem"]: f for f in list_config_facts(configs_dir=tmp_path)}
    assert facts["config-1.0"]["profile_aware"] is True
    assert facts["config-1.0"]["compaction_interval"] == 3


def test_a_variant_is_attached_to_the_model_it_declares_and_to_no_other(tmp_path):
    """Direct test of the variants→model join, against a catalog built here.

    Not routed through the shipped catalog: a helper that SKIPS when no model
    has variants turns a broken join into a green skip, which is the failure
    mode a mutation control exists to catch.
    """
    rows = list_models()
    assert len(rows) >= 2, "need two pickable models to prove exclusivity"
    mine, other = rows[0], rows[1]
    (tmp_path / "cat.yaml").write_text(yaml.safe_dump({
        "version": 99, "reviewed": "2026-01-01",
        "defaults": {"reasoning_efforts": [], "cache_mode": "implicit"},
        "variants": {
            "arm-a": {"model": mine["openrouter_id"], "settings": {}},
            "arm-b": {"model": mine["openrouter_id"], "settings": {}},
            "orphan": {"model": "vendor/not-in-the-registry", "settings": {}},
        },
        "profiles": {},
    }))
    by_model = {
        r["model"]: r
        for r in list_provider_profiles(profile_path=tmp_path / "cat.yaml")["profiles"]
    }
    assert by_model[mine["model"]]["variants"] == ["arm-a", "arm-b"]
    assert by_model[other["model"]]["variants"] == []
    # A variant naming a model that is not in the registry is simply unreachable
    # — it must not attach itself to an unrelated row (finding #28's shape).
    assert not any("orphan" in r["variants"] for r in by_model.values())


def test_the_append_config_exposes_a_compaction_interval_and_legacy_does_not(api):
    """Finding #29: the interval was authored in the YAML and served nowhere.

    Asserts it is a positive int, not WHICH int — 20 is a tuned value.
    """
    facts = {c["stem"]: c for c in api.get("/api/profiles").json()["configs"]}
    every = facts[_append_stem()]["compaction_interval"]
    assert isinstance(every, int) and every > 0
    assert facts[_legacy_stem()]["compaction_interval"] is None


def test_a_missing_profile_catalog_degrades_to_unprofiled_rows(tmp_path):
    """The route must not 500 when the catalog is unreadable.

    Every model then reads as unprofiled with an empty ladder — which the dialog
    treats as "no constraint", i.e. exactly the pre-profile behaviour.
    """
    out = list_provider_profiles(profile_path=tmp_path / "missing.yaml")
    assert out["profiles"], "still one row per model"
    assert all(row["unprofiled"] for row in out["profiles"])
    assert all(row["reasoning_efforts"] == [] for row in out["profiles"])
    assert all(row["variants"] == [] for row in out["profiles"])


# ── the API door ──────────────────────────────────────────────────────────────


def test_a_named_variant_is_accepted_on_an_append_config(api):
    model, variants = _model_with_variants()
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(model),
        "config": _append_stem(), "provider_profile": variants[0],
    })
    assert r.status_code == 201, r.json()
    assert r.json()["provider_profile"] == variants[0]


def test_a_run_without_a_variant_is_byte_identical_to_before(api):
    """The without-the-trigger control: absent → None, not a defaulted name."""
    model, _ = _model_with_variants()
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(model), "config": _append_stem(),
    })
    assert r.status_code == 201
    assert r.json()["provider_profile"] is None


def test_a_named_variant_is_refused_on_a_legacy_config(api):
    """resolve_provider_profile raises "requires append_compact" — but inside
    build_run_config, after the item was dequeued and its card went active."""
    model, variants = _model_with_variants()
    stem = _legacy_stem()
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(model),
        "config": stem, "provider_profile": variants[0],
    })
    assert r.status_code == 400
    assert stem in r.json()["detail"]


def test_a_variant_belonging_to_another_model_is_refused(api):
    """gemma-replay on kimi-k3. The message names BOTH sides."""
    other = _model_without_variants()
    _, variants = _model_with_variants()
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(other),
        "config": _append_stem(), "provider_profile": variants[0],
    })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert variants[0] in detail and other in detail


def test_an_unknown_variant_names_the_known_ones(api):
    model, variants = _model_with_variants()
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(model),
        "config": _append_stem(), "provider_profile": "no-such-arm",
    })
    assert r.status_code == 400
    assert all(v in r.json()["detail"] for v in variants)


def test_official_refuses_a_variant_rather_than_dropping_it(api):
    """Decision Q3. A 400, unlike config/max_turns which official DROPS: a
    variant asks for different transport, not for a setting official freezes,
    and a silently-dropped one would post a score under a contract the caller
    did not ask for."""
    model, variants = _model_with_variants()
    r = api.post("/api/queue", json={
        "kind": "official", "model": _alias(model), "provider_profile": variants[0],
    })
    assert r.status_code == 400
    assert "official" in r.json()["detail"]


def test_official_still_drops_the_casual_knobs_it_always_dropped(api):
    """The mirror of the test above, and the reason "reject everything unknown"
    is NOT what was implemented: known-but-inapplicable keys keep their
    documented drop semantics."""
    r = api.post("/api/queue", json={
        "kind": "official", "model": _alias(_model_without_variants()),
        "config": "configs/sneaky.yaml", "max_turns": 5, "gameplay": "speed",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["config"] is None and body["max_turns"] is None
    assert body["gameplay"] is None


def test_an_unknown_spec_key_is_a_400_naming_the_accepted_ones(api):
    """Finding #10's tail: a typo'd key used to enqueue a run with the DEFAULT
    for the field it meant to set, and answer 201."""
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(_model_without_variants()), "max_turn": 5,
    })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "max_turn" in detail and "max_turns" in detail


def test_a_typod_key_would_otherwise_have_been_silently_dropped(api):
    """Mutation control for the test above: prove the key really is unknown to
    the enqueue contract, so the 400 is not answering some other objection."""
    assert "max_turn" not in server._ENQUEUE_KEYS
    assert "max_turns" in server._ENQUEUE_KEYS
    assert "provider_profile" in server._ENQUEUE_KEYS


def test_a_continue_may_not_name_a_variant(api):
    """A continue has no config of its own: ``continue_from_run`` reads the
    SOURCE run's, which already carries its resolved ``_provider_profile``, and
    the executor's continue branch never calls ``prepare_config``. A variant
    accepted here would be stored on the item and read by nobody — the card
    would advertise an arm the run does not use."""
    model, variants = _model_with_variants()
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(model),
        "continue_from": "2026-09-01_00-00-00_config-5.0__x",
        "provider_profile": variants[0],
    })
    assert r.status_code == 400
    assert "continue" in r.json()["detail"]


def test_a_continue_without_a_variant_is_untouched(api):
    """The without-the-trigger control: the guard above must not have broken the
    ordinary continue enqueue, whose config is deliberately None."""
    r = api.post("/api/queue", json={
        "kind": "casual", "model": _alias(_model_without_variants()),
        "continue_from": "2026-09-01_00-00-00_config-5.0__x",
    })
    assert r.status_code == 201
    body = r.json()
    assert body["config"] is None and body["provider_profile"] is None


def test_the_batch_route_rejects_a_bad_variant_without_enqueuing_anything(api):
    """All-or-nothing, same as an unknown model. `pokemon queue add` posts here."""
    model, variants = _model_with_variants()
    good = {"kind": "casual", "model": _alias(model), "config": _append_stem()}
    bad = {**good, "provider_profile": "no-such-arm"}
    r = api.post("/api/queue/batch", json={"items": [good, bad]})
    assert r.status_code == 400
    assert api.get("/api/queue").json()["items"] == []


# ── the executor ──────────────────────────────────────────────────────────────


def test_queue_named_profile_reaches_the_wire_and_the_run_name(executor):
    """The whole point of the axis, with and without the trigger.

    WITH a variant: the resolved profile carries its name and the variant's own
    settings, and ``run_name``/``run_label`` carry the marker (finding #31 —
    ``runner.py``'s suffix requires a NAMED variant, and until now the queue
    path could never supply one).
    WITHOUT: no marker, and the BASE profile's settings.
    The two arms must differ in a real transport field, or the marker would be
    decoration on an identical run.
    """
    model, variants = _model_with_variants()
    stem = _append_stem()

    def build(profile):
        item = QueuedRun(
            queue_id="q_t", kind=RunKind.casual, model=_alias(model),
            config=stem, provider_profile=profile, enqueued_at="2026-09-07T00:00:00",
        )
        cfg, _snapshot, _turns = executor.build_run_config(item)
        return cfg

    base = build(None)
    assert base["_provider_profile"].get("name") is None
    for variant in variants:
        assert variant not in base["run_name"]

    resolved = {v: build(v) for v in variants}
    for variant, cfg in resolved.items():
        assert cfg["_provider_profile"]["name"] == variant
        # runner._slug: non-alphanumerics collapse to '-', lowercased.
        assert variant.replace(".", "-").lower() in cfg["run_name"], variant
        assert variant in cfg["run_label"], variant

    # The axis must actually MOVE transport for at least one variant, or the
    # marker would be decoration on a set of identical runs. Not asserted per
    # variant: `gemma-guidance` is deliberately a no-op against its base profile
    # (the base already defaults to `omit_prior`, i.e. guidance behaviour — see
    # the note at the top of configs/provider-profiles.yaml), so it exists to
    # NAME an arm in the run name rather than to change one.
    def diff(cfg):
        return {
            k for k in cfg["_provider_profile"]
            if k != "name"
            and cfg["_provider_profile"].get(k) != base["_provider_profile"].get(k)
        }

    assert any(diff(cfg) for cfg in resolved.values()), (
        "no variant overrode anything — the A/B has one arm"
    )


def test_the_official_branch_refuses_a_hand_edited_variant(executor):
    """Defence in depth for decision Q3. The API refuses this, so it can only
    arrive via a hand-edited queue.json or an item enqueued before that rule —
    and it must still not post a score under a variant contract."""
    model, variants = _model_with_variants()
    item = QueuedRun(
        queue_id="q_o", kind=RunKind.official, model=_alias(model),
        provider_profile=variants[0], enqueued_at="2026-09-07T00:00:00",
    )
    with pytest.raises(ValueError, match="official"):
        executor.build_run_config(item)


def test_an_official_run_without_a_variant_still_builds(executor):
    """The without-the-trigger control for the guard above: the guard must not
    have made every official run unbuildable."""
    item = QueuedRun(
        queue_id="q_o2", kind=RunKind.official, model=_alias(_model_without_variants()),
        enqueued_at="2026-09-07T00:00:00",
    )
    cfg, snapshot, _turns = executor.build_run_config(item)
    assert cfg["agent_type"] == APPEND_AGENT_TYPE
    assert snapshot == executor.canonical_save


def test_a_dispatch_failure_records_the_config_and_the_variant():
    """Finding #5b's payload. Without these the strip can say a run died but
    not WHICH of two same-model items it was — and the two commonest dispatch
    failures are about exactly these two fields."""
    ex = RunExecutor.__new__(RunExecutor)
    ex.last_error = None
    ex._notify_control = lambda: None
    item = QueuedRun(
        queue_id="q_f", kind=RunKind.casual, model="m(high)",
        config="config-5.0", provider_profile="gemma-replay",
        enqueued_at="2026-09-07T00:00:00",
    )
    ex._record_failure(item, "boom")
    assert ex.last_error["config"] == "config-5.0"
    assert ex.last_error["provider_profile"] == "gemma-replay"
    assert ex.last_error["model"] == "m(high)"
    assert ex.last_error["error"] == "boom"
    assert ex.last_error["at"]


# ── the effort guard ──────────────────────────────────────────────────────────


def test_every_registry_level_is_currently_legal_on_its_profile():
    """WHY the effort guard below needs a synthetic catalog to fire.

    Wave 1 pruned the registry to the levels each endpoint actually supports and
    added a test pinning ``thinking_levels ⊆ reasoning_efforts``
    (test_model_registry_collapse.py). The consequence: NO registry-valid
    selection can trip the append path's effort check today — an illegal effort
    like ``kimi-k3(xhigh)`` is refused one layer earlier, by
    ``_validate_model_alias``, on EVERY config including the legacy ones. The
    guard is therefore defence in depth against a future registry or profile
    edit, and this test states the invariant that makes it currently unreachable
    so a change to either file surfaces here rather than at dispatch.
    """
    rows = {r["model"]: r for r in list_provider_profiles()["profiles"]}
    registry = _load_models_registry()
    for model, row in rows.items():
        if row["unprofiled"] or not row["reasoning_efforts"]:
            continue
        levels = set(model_thinking_levels(registry[model]))
        assert levels <= set(row["reasoning_efforts"]), model


def test_an_illegal_effort_is_a_400_naming_the_legal_list(api, tmp_path):
    """Finding #5's front half, exercised through the REAL resolver.

    The invariant above means the condition cannot be produced from the shipped
    registry, so the profile catalog is narrowed in a temp copy — the ONE thing
    that has to be synthetic — and pointed at through a temp config's
    ``provider_profiles.path``. Everything else is real: the route, the
    validator, ``load_config``, ``resolve_provider_profile``.
    """
    model = _model_without_variants()
    entry = _load_models_registry()[model]
    levels = model_thinking_levels(entry)
    if entry.get("reasoning_type") != "effort" or len(levels) < 2:
        pytest.skip("need an effort-tiered model with two or more levels")

    catalog = yaml.safe_load(open("configs/provider-profiles.yaml"))
    orid = entry["openrouter_id"]
    keep, drop = levels[-1], levels[0]
    catalog["profiles"][orid]["reasoning_efforts"] = [keep]
    catalog["profiles"][orid]["reasoning_default"] = {"effort": keep}
    (tmp_path / "profiles.yaml").write_text(yaml.safe_dump(catalog))

    cfg = yaml.safe_load(open(f"configs/{_append_stem()}.yaml"))
    cfg["player_agent"]["provider_profiles"]["path"] = str(tmp_path / "profiles.yaml")
    narrowed = tmp_path / "config-narrowed.yaml"
    narrowed.write_text(yaml.safe_dump(cfg))

    r = api.post("/api/queue", json={
        "kind": "casual", "model": f"{model}({drop})", "config": str(narrowed),
    })
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert drop in detail and keep in detail

    # Mutation control: the SAME route, the SAME narrowed config, the one level
    # the narrowed profile still allows. If the 400 above came from anything
    # other than the effort check, this would fail too.
    r2 = api.post("/api/queue", json={
        "kind": "casual", "model": f"{model}({keep})", "config": str(narrowed),
    })
    assert r2.status_code == 201, r2.json()

    # ...and the same illegal level on the UNNARROWED append config is fine, so
    # the rejection is the profile's, not the registry's.
    r3 = api.post("/api/queue", json={
        "kind": "casual", "model": f"{model}({drop})", "config": _append_stem(),
    })
    assert r3.status_code == 201, r3.json()


def test_an_illegal_effort_is_still_refused_at_dispatch(executor, tmp_path):
    """The late ValueError inside build_run_config STAYS. A queue.json can be
    hand-edited, and an item enqueued before a catalog edit must be refused
    rather than dispatched under an effort its endpoint does not define."""
    model = _model_without_variants()
    entry = _load_models_registry()[model]
    levels = model_thinking_levels(entry)
    if entry.get("reasoning_type") != "effort" or len(levels) < 2:
        pytest.skip("need an effort-tiered model with two or more levels")

    catalog = yaml.safe_load(open("configs/provider-profiles.yaml"))
    catalog["profiles"][entry["openrouter_id"]]["reasoning_efforts"] = [levels[-1]]
    (tmp_path / "profiles.yaml").write_text(yaml.safe_dump(catalog))
    cfg = yaml.safe_load(open(f"configs/{_append_stem()}.yaml"))
    cfg["player_agent"]["provider_profiles"]["path"] = str(tmp_path / "profiles.yaml")
    narrowed = tmp_path / "config-narrowed.yaml"
    narrowed.write_text(yaml.safe_dump(cfg))

    item = QueuedRun(
        queue_id="q_e", kind=RunKind.casual, model=f"{model}({levels[0]})",
        config=str(narrowed), enqueued_at="2026-09-07T00:00:00",
    )
    with pytest.raises(ValueError, match="reasoning effort"):
        executor.build_run_config(item)


def test_an_illegal_level_is_refused_on_the_legacy_config_too_but_earlier(api):
    """The claim this test exists to REFUTE: that ``kimi-k3(xhigh)`` is a 400 on
    the append config and accepted on the legacy one. It is a 400 on BOTH, from
    ``_validate_model_alias``, because wave 1 removed ``xhigh`` from the
    registry entirely. Pinned so the distinction is not re-invented.
    """
    registry = _load_models_registry()
    model = _model_without_variants()
    legal = set(model_thinking_levels(registry[model]))
    bogus = next(
        (lvl for lvl in ("xhigh", "max", "high", "medium", "low", "minimal")
         if lvl not in legal),
        None,
    )
    if bogus is None:
        pytest.skip("this model offers every level name — no bogus level available")
    for stem in (_append_stem(), _legacy_stem()):
        r = api.post("/api/queue", json={
            "kind": "casual", "model": f"{model}({bogus})", "config": stem,
        })
        assert r.status_code == 400, stem
        assert "unknown model" in r.json()["detail"], stem
