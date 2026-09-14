"""Tests for the online-leaderboard publisher (src/app/publish.py).

Everything with a side effect is injected, so the whole publish/unpublish path
runs here against a fake S3 client and a real git repo with a bare "origin" in
a temp dir. Nothing touches the network; the SPA build and the URL verify are
stubs that record what they were asked to do.

The leak audit gets a MUTATION control: the same fixture run publishes clean,
then refuses once a key is planted in its summary — and refuses BEFORE any
upload, which is the property the audit exists for.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.app import publish as pub
from src.app.trace_build import TRACE_VERSION

PUBLIC = "https://pub-0123456789abcdef0123456789abcdef.r2.dev"


# ───────────────────────────── fakes + fixtures ─────────────────────────────


class FakeS3:
    """The subset of boto3's S3 client the publisher calls."""

    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}   # key → {size, content_type}

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.objects[Key] = {
            "bucket": Bucket,
            "size": Path(Filename).stat().st_size,
            "content_type": (ExtraArgs or {}).get("ContentType"),
        }

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        # page by 2 so the continuation loop is exercised
        start = int(ContinuationToken or 0)
        page = keys[start:start + 2]
        truncated = start + 2 < len(keys)
        resp = {"Contents": [{"Key": k} for k in page], "IsTruncated": truncated}
        if truncated:
            resp["NextContinuationToken"] = str(start + 2)
        return resp

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A working repo on `main` with one commit, and a bare `origin` it pushes to."""
    origin = tmp_path / "origin.git"
    _git("init", "--bare", "-q", str(origin), cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    _git("init", "-q", "-b", "main", cwd=work)
    _git("config", "user.email", "t@example.com", cwd=work)
    _git("config", "user.name", "t", cwd=work)
    (work / "README.md").write_text("x\n")
    _git("add", "README.md", cwd=work)
    _git("commit", "-q", "-m", "init", cwd=work)
    _git("remote", "add", "origin", str(origin), cwd=work)
    _git("push", "-q", "-u", "origin", "main", cwd=work)
    return work


def make_run(root: Path, run_id: str = "2026-09-08_12-00-00_config-5.1__test-model-high", *,
             status: str = "completed", kind: str = "official", video: bool = True, shots: int = 3,
             alias: str = "test-model(high)") -> Path:
    run = root / run_id
    (run / "screenshots").mkdir(parents=True)
    names = [f"{i:05d}_turn_{i}.png" for i in range(1, shots + 1)]
    for n in names:
        (run / "screenshots" / n).write_bytes(b"\x89PNG fake " + n.encode())
    summary = {
        "run_id": run_id, "kind": kind, "status": status,
        "session": {"llm_alias": alias, "llm_model": "test/model", "total_turns": shots,
                    "duration_seconds": 12.5, "started_at": "2026-09-08T12:00:00"},
        "cost": {"total_usd": 0.01},
        "turns": [{"turn": i, "action": ["a"], "reasoning": f"turn {i} reasoning"} for i in range(1, shots + 1)],
    }
    (run / "run_summary.json").write_text(json.dumps(summary))
    turns = [{"turn": i, "screenshot": n, "reasoning": "r"} for i, n in enumerate(names, start=1)]
    trace = {
        "trace_version": TRACE_VERSION, "run_id": run_id, "has_tasks": False, "task_count": 1,
        "turn_count": shots, "compaction_count": 1,
        "tasks": [{
            "task_index": None, "master_input_images": [],
            "turns": turns,
            "timeline": [{"kind": "turn", **turns[0]}, {"kind": "compaction", "number": 1},
                         *({"kind": "turn", **t} for t in turns[1:])],
        }],
    }
    (run / "trace.json").write_text(json.dumps(trace))
    if video:
        (run / "recording.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 100)
    return run


@pytest.fixture
def world(repo, tmp_path):
    s3 = FakeS3()
    store = pub.R2Store(s3, "bucket", PUBLIC)
    log: list[str] = []
    pages = pub.PagesRepo(repo, repo / "local" / "gh-pages", log=log.append)
    run = make_run(tmp_path / "runs")
    return {"s3": s3, "store": store, "pages": pages, "run": run, "repo": repo, "log": log}


def _publish(w, **kw):
    kw.setdefault("secrets", ["sk-or-v1-REALKEYREALKEYREALKEY0000"])
    kw.setdefault("verify", None)
    # Most tests exercise the full shape (trace + screenshots); the default —
    # result + video only — has its own test below.
    kw.setdefault("include_trace", True)
    return pub.publish_run(w["run"], store=w["store"], pages=w["pages"], **kw)


def _clone_board(repo, tmp_path) -> dict:
    """What a fresh clone of origin/gh-pages holds — the reader's view, not the writer's."""
    clone = tmp_path / f"clone-{len(list(tmp_path.iterdir()))}"
    _git("clone", "-q", "-b", "gh-pages", str(repo / ".." / "origin.git"), str(clone), cwd=tmp_path)
    return {p.relative_to(clone).as_posix(): p for p in clone.rglob("*") if p.is_file() and ".git" not in p.parts}


# ───────────────────────────── settings ─────────────────────────────


def test_settings_name_every_missing_var():
    with pytest.raises(pub.PublishError) as exc:
        pub.R2Settings.from_env({"R2_BUCKET": "b"})
    msg = str(exc.value)
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_PUBLIC_BASE_URL"):
        assert name in msg
    assert "R2_BUCKET" not in msg.split("(")[0]


def test_settings_reject_the_s3_endpoint_as_public_url():
    env = {"R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "R2_BUCKET": "b",
           "R2_PUBLIC_BASE_URL": "https://abc.r2.cloudflarestorage.com"}
    with pytest.raises(pub.PublishError, match="S3 endpoint"):
        pub.R2Settings.from_env(env)
    ok = pub.R2Settings.from_env({**env, "R2_PUBLIC_BASE_URL": PUBLIC + "/"})
    assert ok.public_base_url == PUBLIC
    assert ok.endpoint_url == "https://a.r2.cloudflarestorage.com"


def test_pages_url_from_remote_and_base_path():
    assert pub.pages_base_url("https://github.com/AndreasVig/ai-plays-pokemon.git") == "https://andreasvig.github.io/ai-plays-pokemon/"
    assert pub.pages_base_url("git@github.com:andreasvig/ai-plays-pokemon.git") == "https://andreasvig.github.io/ai-plays-pokemon/"
    assert pub.pages_base_url("https://x/y", "https://pokebench.example.com") == "https://pokebench.example.com/"
    assert pub.base_path("https://andreasvig.github.io/ai-plays-pokemon/") == "/ai-plays-pokemon/"
    assert pub.base_path("https://pokebench.example.com/") == "/"
    with pytest.raises(pub.PublishError, match="PAGES_BASE_URL"):
        pub.pages_base_url("https://gitlab.com/a/b.git")


# ───────────────────────────── leak audit ─────────────────────────────


def test_secret_values_skip_public_names_and_short_values():
    env = {"OPENROUTER_API_KEY": "sk-or-v1-abcdefghijklmnop", "R2_BUCKET": "ai-plays-pokemon",
           "R2_PUBLIC_BASE_URL": PUBLIC, "R2_SECRET_ACCESS_KEY": "s3cr3ts3cr3t", "PORT": "3420",
           "R2_ACCOUNT_ID": "0123456789abcdef"}
    vals = pub.secret_values(env)
    assert "sk-or-v1-abcdefghijklmnop" in vals
    assert "s3cr3ts3cr3t" in vals
    assert "0123456789abcdef" in vals          # the account id is not public
    assert "ai-plays-pokemon" not in vals
    assert PUBLIC not in vals
    assert "3420" not in vals


def test_audit_hits_env_values_key_shapes_and_home_paths_and_masks_them():
    secrets = ["s3cr3ts3cr3t"]
    text = "\n".join([
        "clean line",
        'token: "s3cr3ts3cr3t"',
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123",
        "saved to /Users/someone/Desktop/x.png",
        "temp /private/tmp/claude-501/foo",
        "key sk-or-v1-0e6f44aaaaaaaaaaaaaaaaaaaaaaaaaa",
    ])
    hits = pub.audit_files({"summary.json": text}, secrets)
    lines = sorted(h.line for h in hits)
    assert lines == [2, 3, 4, 5, 6]
    whys = {h.why for h in hits}
    assert {"value from .env", "bearer token", "macOS home path", "temp dir path", "OpenRouter/OpenAI key"} <= whys
    # the excerpt never echoes the full secret back
    for h in hits:
        assert "s3cr3ts3cr3t" not in h.excerpt
        assert "sk-or-v1-0e6f44aaaaaaaaaaaaaaaaaaaaaaaaaa" not in h.excerpt
    assert str(hits[0]).startswith("summary.json:2:")


def test_audit_clean_text_passes():
    assert pub.audit_files({"a": "Bearer of bad news; ask Oak, /Users is a word, sk-tiny"}, ["zzzzzzzzzz"]) == []


def test_read_env_file(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# c\nA=1\nB = 'two words'\nC=\"q\"\n\nBAD\nR2_PUBLIC_BASE_URL=https://x/\n")
    assert pub.read_env_file(p) == {"A": "1", "B": "two words", "C": "q", "R2_PUBLIC_BASE_URL": "https://x/"}
    assert pub.read_env_file(tmp_path / "missing") == {}


def test_public_benchmarks_is_first_badge_only():
    rows = [{"id": "pokebench-easy"}, {"id": "pokebench-first-badge", "name": "x"}, {"id": "pokebench-full"}]
    assert pub.public_benchmarks(rows) == [{"id": "pokebench-first-badge", "name": "x"}]


# ───────────────────────────── trace rewrite ─────────────────────────────


def test_rewrite_trace_points_turns_and_timeline_at_r2_and_is_idempotent():
    trace = {"tasks": [{"turns": [{"screenshot": "00001_turn_1.png"}, {"screenshot": None}],
                        "timeline": [{"kind": "turn", "screenshot": "00001_turn_1.png"},
                                     {"kind": "compaction"},
                                     {"kind": "turn", "screenshot": "local/runs/x/screenshots/00002_turn_2.png"}]}]}
    out, names = pub.rewrite_trace(trace, PUBLIC + "/runs/x/screenshots/")
    assert names == ["00001_turn_1.png", "00002_turn_2.png"]
    assert out["tasks"][0]["turns"][0]["screenshot"] == PUBLIC + "/runs/x/screenshots/00001_turn_1.png"
    assert out["tasks"][0]["turns"][1]["screenshot"] is None
    assert out["tasks"][0]["timeline"][2]["screenshot"] == PUBLIC + "/runs/x/screenshots/00002_turn_2.png"
    assert trace["tasks"][0]["turns"][0]["screenshot"] == "00001_turn_1.png", "input not mutated"
    again, names2 = pub.rewrite_trace(out, PUBLIC + "/runs/x/screenshots")
    assert again == out and names2 == names


# ───────────────────────────── gh-pages repo ─────────────────────────────


def test_pages_repo_creates_orphan_branch_then_reuses_it(world, tmp_path):
    pages = world["pages"]
    pages.ensure()
    assert (pages.worktree / ".nojekyll").exists()
    pages.upsert_row({"run_id": "r1", "started_at": "2026-01-01"})
    pages.upsert_row({"run_id": "r0", "started_at": "2026-01-02"})
    assert pages.commit_and_push("publish r1") is True
    assert pages.commit_and_push("again") is False, "nothing to commit → no commit"
    # the branch is an orphan: gh-pages history does not include main's commit
    log = _git("log", "--oneline", "gh-pages", cwd=world["repo"]).stdout.strip().splitlines()
    assert len(log) == 1 and "publish r1" in log[0]
    files = _clone_board(world["repo"], tmp_path)
    rows = json.loads(files["data/leaderboard.json"].read_text())
    assert [r["run_id"] for r in rows] == ["r0", "r1"], "newest started_at first"
    # second ensure on the existing worktree fast-forwards and is a no-op
    pages.ensure()
    # a second machine (a fresh clone of main) picks up the REMOTE branch
    clone = tmp_path / "machine-2"
    _git("clone", "-q", str(world["repo"] / ".." / "origin.git"), str(clone), cwd=tmp_path)
    other = pub.PagesRepo(clone, clone / "local" / "gh-pages", log=lambda m: None)
    other.ensure()
    assert other.read_board()[1]["run_id"] == "r1"


def test_pages_repo_refuses_a_dirty_worktree(world):
    pages = world["pages"]
    pages.ensure()
    pages.upsert_row({"run_id": "r1"})
    pages.commit_and_push("x")
    (pages.worktree / "stray.txt").write_text("half-finished")
    with pytest.raises(pub.PublishError, match="uncommitted"):
        pages.ensure()


def test_sync_site_replaces_the_bundle_and_keeps_data(world, tmp_path):
    pages = world["pages"]
    pages.ensure()
    pages.upsert_row({"run_id": "r1"})
    (pages.worktree / "assets").mkdir()
    (pages.worktree / "assets" / "old.js").write_text("old")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>new</html>")
    (dist / "assets" / "new.js").write_text("new")
    (dist / "favicon.svg").write_text("<svg/>")
    pages.sync_site(dist)
    assert (pages.worktree / "index.html").read_text() == "<html>new</html>"
    assert (pages.worktree / "404.html").read_text() == "<html>new</html>", "deep links boot the SPA"
    assert not (pages.worktree / "assets" / "old.js").exists()
    assert (pages.worktree / "assets" / "new.js").exists()
    assert (pages.worktree / "favicon.svg").exists()
    assert pages.read_board()[0]["run_id"] == "r1", "data/ survives a bundle sync"
    with pytest.raises(pub.PublishError, match="index.html"):
        pages.sync_site(tmp_path / "empty")


# ───────────────────────────── publish / unpublish ─────────────────────────────


def test_publish_end_to_end(world, tmp_path):
    verified: list[tuple[str, str]] = []
    built: list[Path] = []

    def build(worktree):
        built.append(worktree)
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<html/>")
        (dist / "assets" / "app.js").write_text("js")
        return dist

    res = _publish(world, benchmarks=[{"id": "pokebench-easy"}], build_site=build,
                   verify=lambda u, t: verified.append((u, t)),
                   pages_url="https://andreasvig.github.io/ai-plays-pokemon/")
    run_id = world["run"].name
    # R2 got exactly the video + the referenced screenshots, typed
    s3 = world["s3"]
    assert s3.objects[f"runs/{run_id}/recording.mp4"]["content_type"] == "video/mp4"
    shots = [k for k in s3.objects if "/screenshots/" in k]
    assert len(shots) == 3 and all(s3.objects[k]["content_type"] == "image/png" for k in shots)
    assert res.video_url == f"{PUBLIC}/runs/{run_id}/recording.mp4"
    assert res.page_url == "https://andreasvig.github.io/ai-plays-pokemon/models/test-model"   # the model page, not the run
    assert res.committed is True
    # verify saw the video and one screenshot, both typed
    assert verified[0] == (res.video_url, "video/mp4")
    assert verified[1] == (f"{PUBLIC}/runs/{run_id}/screenshots/00001_turn_1.png", "image/png")
    assert built == [world["pages"].worktree]
    # what a reader clones
    files = _clone_board(world["repo"], tmp_path)
    rows = json.loads(files["data/leaderboard.json"].read_text())
    assert len(rows) == 1
    row = rows[0]
    assert row["run_id"] == run_id and row["video_url"] == res.video_url
    assert row["model"] == "test-model(high)" and row["status"] == "completed" and row["turns"] == 3
    assert row["has_recording"] is True and row["trace_published"] is True
    assert row["screenshots_base_url"] == f"{PUBLIC}/runs/{run_id}/screenshots"
    assert row["published_at"].endswith("+00:00")
    trace = json.loads(files[f"data/runs/{run_id}/trace.json"].read_text())
    assert trace["tasks"][0]["turns"][0]["screenshot"] == f"{PUBLIC}/runs/{run_id}/screenshots/00001_turn_1.png"
    assert trace["tasks"][0]["timeline"][2]["screenshot"].startswith(PUBLIC)
    assert json.loads(files[f"data/runs/{run_id}/summary.json"].read_text())["run_id"] == run_id
    assert json.loads(files["data/benchmarks.json"].read_text()) == [{"id": "pokebench-easy"}]
    assert files["index.html"].read_text() == "<html/>" and "404.html" in files and "assets/app.js" in files
    assert ".nojekyll" in files
    # the local run folder is untouched: the trace on disk still has basenames
    local_trace = json.loads((world["run"] / "trace.json").read_text())
    assert local_trace["tasks"][0]["turns"][0]["screenshot"] == "00001_turn_1.png"


def test_republish_is_one_row_and_idempotent_uploads(world, tmp_path):
    _publish(world)
    n_objects = len(world["s3"].objects)
    res2 = _publish(world, now=None)
    assert len(world["s3"].objects) == n_objects
    rows = world["pages"].read_board()
    assert len(rows) == 1 and rows[0]["run_id"] == res2.run_id
    # published_at moved forward, nothing else duplicated
    files = _clone_board(world["repo"], tmp_path)
    assert len(json.loads(files["data/leaderboard.json"].read_text())) == 1


def test_publish_without_video_and_no_video_flag(world, tmp_path):
    (world["run"] / "recording.mp4").unlink()
    res = _publish(world)
    assert res.video_url is None and res.row["has_recording"] is False
    assert not any(k.endswith(".mp4") for k in world["s3"].objects)
    # a run WITH a video published with --no-video: same result
    run2 = make_run(tmp_path / "runs", "2026-09-08_13-00-00_config-5.1__other-model", alias="other-model(high)")
    res2 = pub.publish_run(run2, store=world["store"], pages=world["pages"], secrets=[], include_video=False)
    assert res2.video_url is None and res2.row["has_recording"] is False
    assert len(world["pages"].read_board()) == 2


def test_default_publish_is_result_and_video_only(world, tmp_path):
    """Andreas 2026-09-08: no traces on the live page. The default sends the row,
    a summary WITHOUT its per-turn list, and the video — no trace, no screenshots."""
    res = pub.publish_run(world["run"], store=world["store"], pages=world["pages"], secrets=[])
    run_id = world["run"].name
    assert res.screenshots == [] and res.row["trace_published"] is False and res.row["screenshots_base_url"] is None
    assert set(world["s3"].objects) == {f"runs/{run_id}/recording.mp4"}, "only the video went to R2"
    files = _clone_board(world["repo"], tmp_path)
    assert f"data/runs/{run_id}/trace.json" not in files
    summary = json.loads(files[f"data/runs/{run_id}/summary.json"].read_text())
    assert "turns" not in summary, "per-turn reasoning stays on the machine"
    assert summary["session"]["total_turns"] == 3 and summary["cost"]["total_usd"] == 0.01, "the result survives"
    # the local file still has its turns
    assert "turns" in json.loads((world["run"] / "run_summary.json").read_text())
    # the SPA's static adapter would 404 on the trace → Report renders no turn section (tasks = [])


def test_with_trace_then_default_drops_the_stale_trace(world):
    _publish(world)
    assert (world["pages"].run_dir(world["run"].name) / "trace.json").exists()
    res = _publish(world, include_trace=False)
    assert res.screenshots == [] and res.row["trace_published"] is False and res.row["screenshots_base_url"] is None
    assert not (world["pages"].run_dir(world["run"].name) / "trace.json").exists()


def test_publish_site_pushes_the_bundle_and_leaves_every_row_alone(world, tmp_path):
    before = _publish(world, include_trace=False).row
    calls = []

    def build(worktree):
        calls.append(worktree)
        dist = tmp_path / "dist2"
        (dist / "assets").mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text("<html>v2</html>")
        (dist / "assets" / "app-v2.js").write_text("v2")
        return dist

    uploads = dict(world["s3"].objects)
    assert pub.publish_site(pages=world["pages"], build_site=build, benchmarks=[{"id": "pokebench-first-badge"}], log=world["log"].append)
    assert calls == [world["pages"].worktree]
    files = _clone_board(world["repo"], tmp_path)
    assert files["index.html"].read_text() == "<html>v2</html>"
    assert files["404.html"].read_text() == "<html>v2</html>"
    assert "assets/app-v2.js" in files
    board = json.loads(files["data/leaderboard.json"].read_text())
    assert board == [before], "the row survives untouched — this is the point of the verb"
    assert world["s3"].objects == uploads, "nothing uploaded"
    # a second rebuild with the same bundle pushes nothing
    assert pub.publish_site(pages=world["pages"], build_site=build, log=world["log"].append) is False


def test_publish_refuses_unfinished_or_missing_runs(world, tmp_path):
    running = make_run(tmp_path / "runs", "2026-09-08_14-00-00_config-5.1__live", status="running")
    with pytest.raises(pub.PublishError, match="status is running"):
        pub.publish_run(running, store=world["store"], pages=world["pages"], secrets=[])
    # Only official completed/terminated runs go online — the History there has
    # no kind badge and no status column, so nothing else may reach it.
    casual = make_run(tmp_path / "runs", "2026-09-08_14-10-00_config-5.1__cas", kind="casual")
    with pytest.raises(pub.PublishError, match="kind is casual"):
        pub.publish_run(casual, store=world["store"], pages=world["pages"], secrets=[])
    for status in ("crashed", "cancelled"):
        broken = make_run(tmp_path / "runs", f"2026-09-08_14-20-00_config-5.1__{status}", status=status)
        with pytest.raises(pub.PublishError, match=f"status is {status}"):
            pub.publish_run(broken, store=world["store"], pages=world["pages"], secrets=[])
    (tmp_path / "runs" / "empty").mkdir()
    with pytest.raises(pub.PublishError, match="run_summary.json"):
        pub.publish_run(tmp_path / "runs" / "empty", store=world["store"], pages=world["pages"], secrets=[])
    assert world["s3"].objects == {}, "every refusal happened before the first upload"
    # and the other terminal status is accepted
    terminated = make_run(tmp_path / "runs", "2026-09-08_14-30-00_config-5.1__term", status="terminated")
    assert pub.publish_run(terminated, store=world["store"], pages=world["pages"], secrets=[],
                           verify=None).run_id == terminated.name


def test_publish_refuses_when_a_trace_screenshot_is_missing_on_disk(world):
    (world["run"] / "screenshots" / "00002_turn_2.png").unlink()
    with pytest.raises(pub.PublishError, match="not on disk"):
        _publish(world)
    assert world["s3"].objects == {}


def test_leak_audit_control_refuses_before_any_upload(world):
    """Mutation control: the same run publishes clean, then a planted key refuses it."""
    _publish(world)
    world["s3"].objects.clear()
    summary = world["run"] / "run_summary.json"
    data = json.loads(summary.read_text())
    # Planted in a field that IS published (`error` became private on 2026-09-09):
    # the goal text rides on summary.json and the leaderboard row.
    data["session"]["task"] = "debug: Authorization sk-or-v1-PLANTEDPLANTEDPLANTEDPLANTED0000"
    summary.write_text(json.dumps(data, indent=1))
    with pytest.raises(pub.PublishError) as exc:
        _publish(world)
    msg = str(exc.value)
    assert "leak audit" in msg and "summary.json" in msg and "OpenRouter/OpenAI key" in msg
    assert "PLANTEDPLANTEDPLANTED" not in msg, "the refusal must not echo the secret"
    assert world["s3"].objects == {}, "refused before the first upload"
    # the exact .env value is caught too, even when it matches no key shape
    data["session"]["task"] = "the value was quietsecretvalue42"
    summary.write_text(json.dumps(data))
    with pytest.raises(pub.PublishError, match="value from .env"):
        _publish(world, secrets=["quietsecretvalue42"])
    # and a home path in the REWRITTEN trace is caught after the rewrite
    data["error"] = "clean"
    summary.write_text(json.dumps(data))
    trace = json.loads((world["run"] / "trace.json").read_text())
    trace["tasks"][0]["turns"][0]["reasoning"] = "see /Users/someone/Desktop/notes.txt"
    (world["run"] / "trace.json").write_text(json.dumps(trace))
    with pytest.raises(pub.PublishError, match="macOS home path"):
        _publish(world)


def test_unpublish_removes_objects_row_and_files(world, tmp_path):
    _publish(world)
    run_id = world["run"].name
    other = make_run(tmp_path / "runs", "2026-09-08_15-00-00_config-5.1__keep", alias="test-model(low)")   # another level of the same model: allowed
    pub.publish_run(other, store=world["store"], pages=world["pages"], secrets=[])
    assert len(world["s3"].objects) == 5   # 4 (trace publish) + the other run's video
    result = pub.unpublish_run(run_id, store=world["store"], pages=world["pages"], log=lambda m: None)
    assert result == {"r2_deleted": 4, "row_removed": True, "files_removed": True, "committed": True}
    assert set(world["s3"].objects) == {k for k in world["s3"].objects if other.name in k}
    assert len(world["s3"].objects) == 1, "the other run (default publish) has only its video"

    files = _clone_board(world["repo"], tmp_path)
    rows = json.loads(files["data/leaderboard.json"].read_text())
    assert [r["run_id"] for r in rows] == [other.name]
    assert not any(run_id in k for k in files)
    # unpublishing something never published is a clean no-op
    again = pub.unpublish_run("never-there", store=world["store"], pages=world["pages"], log=lambda m: None)
    assert again == {"r2_deleted": 0, "row_removed": False, "files_removed": False, "committed": False}


CATALOG = [
    {"model": "test-model", "openrouter_id": "test/model", "vendor": "test", "reasoning_type": "effort",
     "thinking_levels": ["xhigh", "high", "low"], "released": "2026-01-01"},
    {"model": "never-run", "openrouter_id": "x/never", "vendor": "x", "reasoning_type": "none", "thinking_levels": [], "released": None},
]


def test_publish_refuses_a_second_run_for_a_level_already_on_the_board(world, tmp_path):
    """One run per model + thinking level (2026-09-14, model pages). A second run
    for test-model(high) is refused BEFORE any upload, naming the run to
    unpublish; after `unpublish` it goes up. The same run republishing itself is
    still fine (its row is replaced, not doubled)."""
    _publish(world)
    first = world["run"].name
    n_objects = len(world["s3"].objects)
    second = make_run(tmp_path / "runs", "2026-09-09_09-00-00_config-5.1__test-model-high-again")
    with pytest.raises(pub.PublishError) as exc:
        pub.publish_run(second, store=world["store"], pages=world["pages"], secrets=[])
    assert first in str(exc.value) and "already on the board" in str(exc.value) and f"pokemon unpublish {first}" in str(exc.value)
    assert len(world["s3"].objects) == n_objects, "refused before the first upload"
    assert [r["run_id"] for r in world["pages"].read_board()] == [first]
    _publish(world)   # the same run again: allowed
    assert len(world["pages"].read_board()) == 1
    pub.unpublish_run(first, store=world["store"], pages=world["pages"], log=lambda m: None)
    res = pub.publish_run(second, store=world["store"], pages=world["pages"], secrets=[])
    assert [r["run_id"] for r in world["pages"].read_board()] == [res.run_id]
    # the pure helper behind it
    rows = [{"run_id": "a", "model": "m(high)"}, {"run_id": "b", "model": "m(low)"}]
    assert pub.board_clash(rows, "m(high)", "a") is None and pub.board_clash(rows, "m(high)", "c") == "a"
    assert pub.board_clash(rows, "m(medium)", "c") is None


def test_dry_run_reports_a_board_clash_and_changes_nothing(world, tmp_path, capsys):
    from types import SimpleNamespace

    from src.cli.publish import _dry_run

    _publish(world)
    second = make_run(tmp_path / "runs", "2026-09-09_09-00-00_config-5.1__test-model-high-again")
    args = SimpleNamespace(with_trace=False, video="full", no_video=False)
    _dry_run(second, world["store"], world["pages"], [], args)
    out = capsys.readouterr().out
    assert "already on the board as " + world["run"].name in out and "publish would refuse" in out
    assert [r["run_id"] for r in world["pages"].read_board()] == [world["run"].name]
    _dry_run(world["run"], world["store"], world["pages"], [], args)   # the same run: no clash line
    assert "already on the board" not in capsys.readouterr().out


def test_models_json_lists_only_models_with_a_row_and_keeps_level_order(world, tmp_path):
    """data/models.json (2026-09-14): the model page lists a model's thinking
    levels from the registry so it can grey out the ones nobody ran. Only
    models with at least one row are exported; the catalog's level order
    (highest first) is kept; unpublishing the last run drops the model."""
    _publish(world, models=CATALOG)
    files = _clone_board(world["repo"], tmp_path)
    models = json.loads(files["data/models.json"].read_text())
    assert [m["model"] for m in models] == ["test-model"]
    assert models[0]["thinking_levels"] == ["xhigh", "high", "low"] and models[0]["vendor"] == "test"
    # publish_site rewrites it too (a catalog edit reaches the site with the next bundle)
    build = lambda wt: None  # noqa: E731
    pub.publish_site(pages=world["pages"], build_site=build, models=CATALOG + [{"model": "test-model-2", "thinking_levels": ["high"]}], log=lambda m: None)
    assert [m["model"] for m in json.loads(world["pages"].data_dir.joinpath("models.json").read_text())] == ["test-model"]
    pub.unpublish_run(world["run"].name, store=world["store"], pages=world["pages"], models=CATALOG, log=lambda m: None)
    assert json.loads(world["pages"].data_dir.joinpath("models.json").read_text()) == []
    # the pure helper: a reasoning_type none model is matched on its bare name
    assert [m["model"] for m in pub.public_models(CATALOG, [{"model": "never-run"}])] == ["never-run"]


def test_verify_public_url_sends_a_named_user_agent(monkeypatch):
    seen = {}

    class Resp:
        status = 200
        headers = {"Content-Type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["ua"] = req.get_header("User-agent")
        seen["url"] = req.full_url
        return Resp()

    monkeypatch.setattr(pub.urllib.request, "urlopen", fake_urlopen)
    assert pub.verify_public_url("https://x/y.mp4", expect_type="video/mp4") == "video/mp4"
    assert seen["ua"] == pub.USER_AGENT and "Python-urllib" not in seen["ua"]
    with pytest.raises(pub.PublishError, match="Content-Type"):
        pub.verify_public_url("https://x/y.mp4", expect_type="image/png")


def test_publish_uploads_the_full_view_by_default_and_the_simple_file_on_request(world, tmp_path):
    """A `both` recording: recording.mp4 IS the full panel (the upload default);
    --video simple picks recording-simple.mp4. Either way the R2 key is
    recording.mp4 and the row says which view it shows."""
    run = world["run"]
    (run / "config.json").write_text(json.dumps({"_record": {"view": "both", "speed": "realtime"}}))
    (run / "recording-simple.mp4").write_bytes(b"simple" * 50)
    res = pub.publish_run(run, store=world["store"], pages=world["pages"], secrets=[])
    key = f"runs/{run.name}/recording.mp4"
    assert res.row["video_view"] == "detailed" and res.video_url.endswith("/recording.mp4")
    assert world["s3"].objects[key]["size"] == (run / "recording.mp4").stat().st_size
    res2 = pub.publish_run(run, store=world["store"], pages=world["pages"], secrets=[], video_file="recording-simple.mp4")
    assert res2.row["video_view"] == "simple" and res2.video_url.endswith("/recording.mp4")
    assert world["s3"].objects[key]["size"] == (run / "recording-simple.mp4").stat().st_size, "same key, other file"
    # asking for the simple file when there is none is a refusal, not a silent fallback
    (run / "recording-simple.mp4").unlink()
    with pytest.raises(pub.PublishError, match="recording-simple.mp4"):
        pub.publish_run(run, store=world["store"], pages=world["pages"], secrets=[], video_file="recording-simple.mp4")
    # a single-view recording reports its own view; no config → unknown
    (run / "config.json").write_text(json.dumps({"_record": {"view": "simple"}}))
    assert pub.recorded_view(run) == "simple"
    (run / "config.json").unlink()
    assert pub.recorded_view(run) is None


def test_refresh_rows_reprojects_published_rows_and_keeps_publish_only_keys(tmp_path):
    """--refresh-rows (2026-09-13): a published row is a cached projection plus the
    publish-only keys. Re-projecting from the local run dir adds what the projection
    learnt since (gate_turns) and leaves video_url / published_at untouched; a row
    whose run dir is gone is kept as it was."""
    import json as _json
    from src.app import publish as pub

    runs = tmp_path / "runs"
    run = runs / "2026-09-12_10-00-00_config-5.1__m-high"
    run.mkdir(parents=True)
    (run / "run_summary.json").write_text(_json.dumps({
        "run_id": run.name, "kind": "official", "status": "terminated", "benchmark": "pokebench-first-badge",
        "session": {"llm_alias": "m(high)", "total_turns": 40, "duration_seconds": 400.0}, "cost": {"total_usd": 1.0},
        "referee": {"gates": [{"id": "left_bedroom", "turn": 3, "status": "done"}, {"id": "left_house", "turn": 9, "status": "done"},
                              {"id": "oaks_lab_entered", "turn": None, "status": "pending"}], "furthest": "left_house"},
    }))
    stale = {"run_id": run.name, "model": "m(high)", "turns": 40, "video_url": "https://r2.example/v.mp4", "published_at": "2026-09-12T10:00:00Z",
             "has_recording": True, "projection_version": 3}
    gone = {"run_id": "2026-09-01_00-00-00_config-5.1__gone", "model": "gone(high)", "turns": 7, "video_url": None}

    class Board:
        def __init__(self): self.rows = [stale, gone]; self.writes = 0
        def read_board(self): return [dict(r) for r in self.rows]
        def write_board(self, rows): self.rows = rows; self.writes += 1

    board = Board()
    assert pub.refresh_rows(board, runs, secrets=["sekrit"], log=lambda m: None) == 1
    assert board.writes == 1
    fresh = next(r for r in board.rows if r["run_id"] == run.name)
    assert fresh["gate_turns"] == {"left_bedroom": 3, "left_house": 9}
    assert fresh["video_url"] == "https://r2.example/v.mp4" and fresh["published_at"] == "2026-09-12T10:00:00Z" and fresh["has_recording"] is True
    assert fresh["projection_version"] >= 4 and "error" not in fresh and "record" not in fresh
    assert next(r for r in board.rows if r["run_id"] == gone["run_id"]) == gone
    # Nothing to change → nothing written.
    assert pub.refresh_rows(board, runs, log=lambda m: None) == 0 and board.writes == 1


def test_sync_site_copies_static_folders_and_never_data(world, tmp_path):
    """public/ folders (logos/) ride along with the bundle and are replaced
    whole; a data/ folder in dist would never overwrite the published rows.
    2026-09-14: the vendor marks were built into dist/logos/ and 404'd live
    because only assets/ was synced."""
    pages = world["pages"]
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "logos").mkdir()
    (dist / "data").mkdir()
    (dist / "index.html").write_text("<html>v1</html>")
    (dist / "logos" / "openai.svg").write_text("<svg/>")
    (dist / "logos" / "stale.svg").write_text("<svg/>")
    (dist / "data" / "leaderboard.json").write_text("[]")
    pages.sync_site(dist)
    assert (pages.worktree / "logos" / "openai.svg").read_text() == "<svg/>"
    assert not (pages.worktree / "data" / "leaderboard.json").exists() or (pages.worktree / "data" / "leaderboard.json").read_text() != "[]"
    # Second build without the stale file: it is gone from the site too.
    (dist / "logos" / "stale.svg").unlink()
    pages.sync_site(dist)
    assert sorted(p.name for p in (pages.worktree / "logos").iterdir()) == ["openai.svg"]

