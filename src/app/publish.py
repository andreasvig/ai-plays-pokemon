"""Publish a finished run to the online leaderboard — the library behind
``pokemon publish`` / ``pokemon unpublish`` (artifacts/online-leaderboard/plan.md).

Two dumb stores, no database:

- **Cloudflare R2** (S3 API) holds the heavy bytes: ``runs/<run_id>/recording.mp4``
  and ``runs/<run_id>/screenshots/<name>.png``, served anonymously from the
  bucket's public ``r2.dev`` host.
- **The ``gh-pages`` branch** (GitHub Pages) holds the static SPA build plus
  ``data/leaderboard.json`` (one row per published run), ``data/benchmarks.json``
  and ``data/runs/<run_id>/summary.json``. Its git history is the audit trail.

**Result and video only** (Andreas, 2026-09-08): the public page shows what a
run scored and its recording, not how the model reasoned. So the default
publish sends no trace and no screenshots, and the published summary is the
run's ``run_summary.json`` with the per-turn ``turns`` list removed. A trace is
opt-in (``include_trace=True`` / ``--with-trace``) and then goes with its
screenshots.

The local run folder stays the source of truth; nothing leaves the machine
unless ``publish`` is run for that run, and the exact outgoing text goes
through :func:`audit_files` first (every ``.env`` secret, key shapes, home
paths). Everything with a side effect is injectable — the S3 client, the git
repo, the site build, the URL verifier — so the orchestration is tested end to
end against a fake bucket and a temp remote without touching the network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from src.app.models import RunKind, RunStatus
from src.app import route
from src.app.projection import project_run_dir

# The User-Agent every anonymous probe sends. Cloudflare answers 403 to
# Python's default ``Python-urllib/3.x`` on ``r2.dev`` for EVERY key, existing
# or missing (observed 2026-09-08; curl, browsers and any named agent get 200),
# so a default-agent probe reads a working bucket as a failed upload.
USER_AGENT = "pokebench-publish/1.0"

# ``.env`` names whose values are public BY DESIGN: the bucket name sits in
# nothing public, but the public base URL is in every rewritten screenshot ref,
# so auditing for it would refuse every publish.
PUBLIC_ENV_NAMES = frozenset({"R2_BUCKET", "R2_PUBLIC_BASE_URL"})

# Files never published (the run's provider payloads and working state live
# here). Listed for the docs and the audit's sanity, not read by code.
NEVER_PUBLISHED = (
    "events.jsonl", "conversation/", "ocr/", "savepoints/", "state.json", "terminal.log",
)

# The public site is ONE benchmark — PokeBench v1 is the first-badge ladder
# (Andreas, 2026-09-08). Only this registry entry is published, so the static
# board scopes itself to it and shows no benchmark picker.
PUBLIC_BENCHMARKS = ("pokebench-first-badge",)

BRANCH = "gh-pages"
WORKTREE_DIRNAME = "gh-pages"            # under local/
SITE_DIST_DIRNAME = "dist-static"        # under src/dashboard/web/


class PublishError(RuntimeError):
    """A refusal or a failure the user has to act on; the CLI prints str(exc)."""


# ───────────────────────────── R2 ─────────────────────────────


@dataclass(frozen=True)
class R2Settings:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base_url: str

    REQUIRED = (
        "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET", "R2_PUBLIC_BASE_URL",
    )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "R2Settings":
        env = os.environ if env is None else env
        missing = [n for n in cls.REQUIRED if not (env.get(n) or "").strip()]
        if missing:
            raise PublishError(
                "missing R2 settings in .env: " + ", ".join(missing)
                + " (see .env.example for where each value lives in the Cloudflare dashboard)"
            )
        base = env["R2_PUBLIC_BASE_URL"].strip().rstrip("/")
        if ".r2.cloudflarestorage.com" in base:
            raise PublishError(
                "R2_PUBLIC_BASE_URL is the S3 endpoint, which only answers signed requests. "
                "Use the bucket's Public Development URL (https://pub-<hash>.r2.dev) or a "
                "custom domain connected to the bucket."
            )
        return cls(
            account_id=env["R2_ACCOUNT_ID"].strip(),
            access_key_id=env["R2_ACCESS_KEY_ID"].strip(),
            secret_access_key=env["R2_SECRET_ACCESS_KEY"].strip(),
            bucket=env["R2_BUCKET"].strip(),
            public_base_url=base,
        )

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def make_r2_client(settings: R2Settings):
    """A boto3 S3 client pointed at R2 (region ``auto``, SigV4)."""
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=settings.endpoint_url,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name="auto",
        config=Config(signature_version="s3v4", retries={"max_attempts": 5}),
    )


class R2Store:
    """The five S3 calls publishing needs, over any S3-like client.

    Tests pass a fake; production passes :func:`make_r2_client`'s client.
    """

    def __init__(self, client, bucket: str, public_base_url: str) -> None:
        self.client = client
        self.bucket = bucket
        self.public_base_url = public_base_url.rstrip("/")

    def url(self, key: str) -> str:
        return f"{self.public_base_url}/{key}"

    def put_file(self, path: Path, key: str, content_type: str) -> str:
        self.client.upload_file(
            str(path), self.bucket, key,
            ExtraArgs={"ContentType": content_type, "CacheControl": "public, max-age=31536000, immutable"},
        )
        return self.url(key)

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kwargs)
            keys.extend(o["Key"] for o in resp.get("Contents", []) or [])
            if not resp.get("IsTruncated"):
                return keys
            token = resp.get("NextContinuationToken")

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_keys(prefix)
        for i in range(0, len(keys), 1000):
            chunk = keys[i:i + 1000]
            self.client.delete_objects(
                Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True}
            )
        return len(keys)


# ───────────────────────────── leak audit ─────────────────────────────

# Key shapes — the same list the 2026-09-08 history audit grepped for.
KEY_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("OpenRouter/OpenAI key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}")),
    ("Slack token", re.compile(r"\bxox[bpsa]-[A-Za-z0-9-]{10,}")),
    ("Perplexity key", re.compile(r"\bpplx-[A-Za-z0-9]{20,}")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{20,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
)

PATH_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("macOS home path", re.compile(r"/Users/[A-Za-z0-9._-]+/")),
    ("temp dir path", re.compile(r"/private/tmp/|/private/var/folders/")),
    ("Linux home path", re.compile(r"/home/[A-Za-z0-9._-]+/")),
)


@dataclass(frozen=True)
class LeakHit:
    file: str
    line: int
    why: str
    excerpt: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}: {self.why} — {self.excerpt}"


def secret_values(env: Mapping[str, str], *, exclude: Iterable[str] = PUBLIC_ENV_NAMES,
                  min_len: int = 8) -> list[str]:
    """Every ``.env`` value worth refusing a publish over.

    Short values (``true``, ``30``, a port) would match everywhere and mean
    nothing; names in ``exclude`` are public by design.
    """
    skip = set(exclude)
    out = []
    for name, value in env.items():
        if name in skip:
            continue
        v = (value or "").strip()
        if len(v) >= min_len and not v.lower().startswith(("your-", "sk-or-v1-your")):
            out.append(v)
    # longest first so an excerpt names the most specific match
    return sorted(set(out), key=len, reverse=True)


def audit_text(name: str, text: str, secrets: Iterable[str]) -> list[LeakHit]:
    hits: list[LeakHit] = []
    secrets = list(secrets)
    for lineno, line in enumerate(text.splitlines(), start=1):
        for s in secrets:
            if s in line:
                hits.append(LeakHit(name, lineno, "value from .env", _excerpt(line, s)))
        for why, rx in KEY_SHAPES + PATH_SHAPES:
            m = rx.search(line)
            if m:
                hits.append(LeakHit(name, lineno, why, _excerpt(line, m.group(0))))
    return hits


def audit_files(files: Mapping[str, str], secrets: Iterable[str]) -> list[LeakHit]:
    """Audit ``{name: text}`` — the exact bytes about to leave the machine."""
    secrets = list(secrets)
    hits: list[LeakHit] = []
    for name, text in files.items():
        hits.extend(audit_text(name, text, secrets))
    return hits


def _excerpt(line: str, needle: str, context: int = 20) -> str:
    """The match in context, with the match itself masked.

    Built around the match rather than sliced from the line and then replaced,
    so a long secret can never survive because the slice cut it in half.
    """
    i = line.find(needle)
    mask = needle[:4] + "…" + needle[-2:] if len(needle) > 8 else "…"
    before = line[max(0, i - context):i].lstrip()
    after = line[i + len(needle):i + len(needle) + context].rstrip()
    return f"{before}{mask}{after}"


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines of a dotenv file, comments and blanks skipped."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


# ───────────────────────────── trace rewrite ─────────────────────────────


def rewrite_trace(trace: dict, screenshots_base_url: str) -> tuple[dict, list[str]]:
    """Point every ``screenshot`` reference at R2 and return the basenames seen.

    The local trace carries basenames the SPA turns into
    ``/api/runs/{id}/screenshots/{name}``; the published trace carries absolute
    URLs so it is self-contained. Refs that are already URLs are left alone,
    which is what makes a re-publish of an already-rewritten trace a no-op.
    Both ``turns`` and the ``timeline`` (turns interleaved with compactions)
    are walked — the Report renders the timeline when present.
    """
    base = screenshots_base_url.rstrip("/")
    names: list[str] = []

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if k == "screenshot" and isinstance(v, str) and v:
                    if v.startswith(("http://", "https://")):
                        out[k] = v
                        names.append(v.rsplit("/", 1)[-1])
                    else:
                        name = Path(v).name
                        names.append(name)
                        out[k] = f"{base}/{name}"
                else:
                    out[k] = walk(v)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    rewritten = walk(trace)
    return rewritten, sorted(set(names))


# ───────────────────────────── gh-pages repo ─────────────────────────────


class PagesRepo:
    """The ``gh-pages`` branch, checked out as a worktree under ``local/``.

    ``ensure()`` gets the worktree to a clean, current checkout of the branch:
    it fetches the remote branch when one exists (so a publish from a second
    machine fast-forwards instead of conflicting) and creates an orphan branch
    the first time. Data files are plain JSON under ``data/``.
    """

    def __init__(self, repo_root: Path, worktree: Path, *, branch: str = BRANCH,
                 remote: str = "origin", log: Callable[[str], None] = print) -> None:
        self.repo_root = Path(repo_root)
        self.worktree = Path(worktree)
        self.branch = branch
        self.remote = remote
        self.log = log

    # -- git plumbing --------------------------------------------------------

    def _git(self, *args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            ["git", "-C", str(cwd or self.repo_root), *args],
            capture_output=True, text=True,
        )
        if check and proc.returncode != 0:
            raise PublishError(f"git {' '.join(args)} failed:\n{proc.stderr.strip() or proc.stdout.strip()}")
        return proc

    def _remote_branch_exists(self) -> bool:
        proc = self._git("ls-remote", "--exit-code", "--heads", self.remote, self.branch, check=False)
        return proc.returncode == 0

    def _local_branch_exists(self) -> bool:
        return self._git("rev-parse", "--verify", "--quiet", f"refs/heads/{self.branch}", check=False).returncode == 0

    def ensure(self) -> None:
        remote_has = self._remote_branch_exists()
        if remote_has:
            self._git("fetch", self.remote, f"{self.branch}:refs/remotes/{self.remote}/{self.branch}")
        if (self.worktree / ".git").exists():
            head = self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.worktree).stdout.strip()
            if head != self.branch:
                raise PublishError(f"{self.worktree} is checked out on {head!r}, expected {self.branch!r}")
            dirty = self._git("status", "--porcelain", cwd=self.worktree).stdout.strip()
            if dirty:
                raise PublishError(
                    f"{self.worktree} has uncommitted changes; a previous publish did not finish. "
                    "Inspect it, then `git -C <worktree> reset --hard` to drop them."
                )
            if remote_has:
                self._git("merge", "--ff-only", f"{self.remote}/{self.branch}", cwd=self.worktree)
            return
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        if self._local_branch_exists():
            self._git("worktree", "add", str(self.worktree), self.branch)
            if remote_has:
                self._git("merge", "--ff-only", f"{self.remote}/{self.branch}", cwd=self.worktree)
        elif remote_has:
            self._git("worktree", "add", "--track", "-b", self.branch, str(self.worktree),
                      f"{self.remote}/{self.branch}")
        else:
            self.log(f"creating orphan branch {self.branch}")
            self._git("worktree", "add", "--orphan", "-b", self.branch, str(self.worktree))
        # Pages runs Jekyll by default, which drops `_`-prefixed paths; opt out.
        (self.worktree / ".nojekyll").write_text("")

    # -- data files ----------------------------------------------------------

    @property
    def data_dir(self) -> Path:
        return self.worktree / "data"

    def board_path(self) -> Path:
        return self.data_dir / "leaderboard.json"

    def read_board(self) -> list[dict]:
        p = self.board_path()
        if not p.is_file():
            return []
        rows = json.loads(p.read_text())
        return rows if isinstance(rows, list) else []

    def write_board(self, rows: list[dict]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        rows = sorted(rows, key=lambda r: (r.get("started_at") or "", r.get("run_id") or ""), reverse=True)
        _write_json(self.board_path(), rows)

    def upsert_row(self, row: dict) -> None:
        rows = [r for r in self.read_board() if r.get("run_id") != row["run_id"]]
        rows.append(row)
        self.write_board(rows)

    def remove_row(self, run_id: str) -> bool:
        rows = self.read_board()
        kept = [r for r in rows if r.get("run_id") != run_id]
        self.write_board(kept)
        return len(kept) != len(rows)

    def run_dir(self, run_id: str) -> Path:
        return self.data_dir / "runs" / run_id

    def write_run_files(self, run_id: str, files: Mapping[str, str]) -> None:
        d = self.run_dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (d / name).write_text(text)

    def remove_run_files(self, run_id: str) -> bool:
        d = self.run_dir(run_id)
        if d.is_dir():
            shutil.rmtree(d)
            return True
        return False

    def write_benchmarks(self, payload: list[dict]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.data_dir / "benchmarks.json", payload)

    def write_models(self, payload: list[dict]) -> None:
        """``data/models.json`` — the model catalog the model pages list levels from."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.data_dir / "models.json", payload)

    # -- site bundle ---------------------------------------------------------

    def sync_site(self, dist: Path) -> None:
        """Copy a Vite build into the worktree root, replacing the old bundle.

        The bundle is replaced (``index.html``, ``404.html``, ``assets/``) and so
        is every other top-level directory the build emits — Vite copies
        ``public/`` (``logos/``, since 2026-09-14) to the dist root; ``data/`` is
        never touched by a build.
        """
        dist = Path(dist)
        index = dist / "index.html"
        if not index.is_file():
            raise PublishError(f"no index.html in {dist}; did the site build run?")
        assets_dst = self.worktree / "assets"
        if assets_dst.is_dir():
            shutil.rmtree(assets_dst)
        if (dist / "assets").is_dir():
            shutil.copytree(dist / "assets", assets_dst)
        shutil.copy2(index, self.worktree / "index.html")
        # GitHub Pages serves 404.html for any path without a file, so a deep
        # link like /history/<run_id> boots the SPA (with a 404 status) instead
        # of a GitHub error page.
        shutil.copy2(index, self.worktree / "404.html")
        for extra in dist.iterdir():
            if extra.name in ("index.html", "assets", "data"):
                continue
            dst = self.worktree / extra.name
            if extra.is_dir():
                # A static folder from public/ (logos/): replace it whole so a
                # renamed or removed file does not linger on the site.
                if dst.is_dir():
                    shutil.rmtree(dst)
                shutil.copytree(extra, dst)
            else:
                shutil.copy2(extra, dst)

    # -- commit + push -------------------------------------------------------

    def commit_and_push(self, message: str) -> bool:
        """Stage everything in the worktree, commit, push. False if nothing changed."""
        self._git("add", "-A", cwd=self.worktree)
        if not self._git("status", "--porcelain", cwd=self.worktree).stdout.strip():
            return False
        self._git("-c", "user.name=pokemon publish", "-c", "user.email=publish@pokebench.local",
                  "commit", "-q", "-m", message, cwd=self.worktree)
        self._git("push", "-u", self.remote, f"{self.branch}:{self.branch}", cwd=self.worktree)
        return True

    def remote_url(self) -> str:
        return self._git("remote", "get-url", self.remote).stdout.strip()


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


# ───────────────────────────── site build ─────────────────────────────


def pages_base_url(remote_url: str, override: str | None = None) -> str:
    """``https://<user>.github.io/<repo>/`` from the origin URL, or ``PAGES_BASE_URL``."""
    if override:
        return override.rstrip("/") + "/"
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", remote_url)
    if not m:
        raise PublishError(
            f"cannot derive a GitHub Pages URL from remote {remote_url!r}; set PAGES_BASE_URL in .env"
        )
    user, repo = m.group(1), m.group(2)
    return f"https://{user.lower()}.github.io/{repo}/"


def base_path(pages_url: str) -> str:
    """The path part Vite needs as ``--base`` (``/ai-plays-pokemon/``)."""
    m = re.match(r"https?://[^/]+(/.*)$", pages_url)
    path = m.group(1) if m else "/"
    return path if path.endswith("/") else path + "/"


def build_static_site(web_dir: Path, out_dir: Path, base: str, log: Callable[[str], None] = print) -> Path:
    """``vite build`` in static mode (``VITE_STATIC=1``) into ``out_dir``."""
    web_dir = Path(web_dir)
    if not (web_dir / "node_modules").is_dir():
        raise PublishError(f"{web_dir} has no node_modules; run `npm install` there first")
    log(f"building static site (base {base})")
    env = {**os.environ, "VITE_STATIC": "1"}
    proc = subprocess.run(
        ["npm", "run", "build", "--", "--base", base, "--outDir", str(out_dir), "--emptyOutDir"],
        cwd=str(web_dir), env=env, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise PublishError(f"site build failed:\n{proc.stderr.strip() or proc.stdout.strip()}")
    return Path(out_dir)


# ───────────────────────────── verification ─────────────────────────────


def verify_public_url(url: str, *, expect_type: str | None = None, timeout: float = 30.0) -> str:
    """Anonymous GET with a named User-Agent; returns the Content-Type.

    A default-agent request would 403 on ``r2.dev`` and report a false failure —
    see ``USER_AGENT``.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        raise PublishError(f"verify {url} → HTTP {exc.code}") from exc
    except Exception as exc:
        raise PublishError(f"verify {url} failed: {exc}") from exc
    if status not in (200, 206):
        raise PublishError(f"verify {url} → HTTP {status}")
    if expect_type and not ctype.startswith(expect_type):
        raise PublishError(f"verify {url} → Content-Type {ctype!r}, expected {expect_type!r}")
    return ctype


# ───────────────────────────── orchestration ─────────────────────────────


@dataclass
class PublishResult:
    run_id: str
    row: dict
    video_url: str | None
    screenshots: list[str]
    page_url: str | None
    committed: bool
    r2_keys: list[str] = field(default_factory=list)


# Per-turn content of run_summary.json that the public page does not show. The
# turn list carries every reasoning string; everything else in the file is a
# result (session, cost, referee scorecard, status).
# `error`/`crash` carry provider error bodies (org ids, remedy URLs); a crashed
# run is never publishable anyway, but the keys stay private regardless.
SUMMARY_PRIVATE_KEYS = ("turns", "error", "crash")
# The flat leaderboard row carries the same two crash fields (RunSummary.error /
# .crash); NOT `turns`, which on the row is the turn COUNT, not the turn list.
ROW_PRIVATE_KEYS = ("error", "crash", "record")


def recorded_view(run_dir: Path, video_file: str = "recording.mp4") -> str | None:
    """Which view a recording file shows: ``detailed`` / ``simple`` / None (unknown).

    Read from the run's ``config.json["_record"]["view"]``, the spec the
    recorder ran with. ``both`` means recording.mp4 is the detailed file and
    recording-simple.mp4 the simple one.
    """
    try:
        record = (json.loads((Path(run_dir) / "config.json").read_text()).get("_record") or {})
    except Exception:
        return None
    view = record.get("view")
    if view == "both":
        return "simple" if video_file == "recording-simple.mp4" else "detailed"
    return view if view in ("simple", "detailed") else None


def route_json(run_dir: Path) -> str | None:
    """``data/runs/<run_id>/route.json`` — the run's tile sequence
    (src/app/route.py), None for a run with no per-input trace. Compact: a
    1 500-turn run is ~12k visits."""
    r = route.load_route(run_dir)
    return json.dumps(r, separators=(",", ":")) if r is not None else None


def public_summary_text(summary_path: Path) -> str:
    """``run_summary.json`` minus its per-turn list — the result, not the reasoning."""
    data = json.loads(Path(summary_path).read_text())
    for k in SUMMARY_PRIVATE_KEYS:
        data.pop(k, None)
    return json.dumps(data, indent=2, ensure_ascii=False)


def public_benchmarks(payload: list[dict]) -> list[dict]:
    """The registry rows the public site gets — see ``PUBLIC_BENCHMARKS``."""
    return [b for b in payload if b.get("id") in PUBLIC_BENCHMARKS]


def public_models(catalog: list[dict], rows: list[dict]) -> list[dict]:
    """The catalog entries the public site gets: models with at least one row.

    The catalog (``src.app.catalog.model_catalog``) holds every model the
    registry knows, run or not; the model page needs the full level list of a
    model that HAS runs so it can grey out the levels nobody ran yet, and
    nothing for a model without a row (there is no page to reach). Ordered by
    the catalog, level order preserved (highest first).
    """
    from src.config import parse_model_alias

    bases = {parse_model_alias(str(r.get("model") or ""))[0] for r in rows}
    return [m for m in catalog if m.get("model") in bases]


def board_clash(rows: list[dict], model: str, run_id: str) -> str | None:
    """The run_id already on the board for this ``model(level)``, if another one is.

    One run per model + thinking level is the board's identity rule (a
    republish of the SAME run replaces its row, which is fine). Publishing a
    second run for a level would put two bars for one identity on every card,
    which is what happened with gemini-3.5-flash-lite(minimal) on 2026-09-12;
    the caller refuses until the old run is unpublished.
    """
    for r in rows:
        if r.get("model") == model and r.get("run_id") != run_id:
            return str(r.get("run_id"))
    return None


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()


def publish_run(
    run_dir: Path,
    *,
    store: R2Store,
    pages: PagesRepo,
    secrets: Iterable[str],
    benchmarks: list[dict] | None = None,
    models: list[dict] | None = None,
    include_video: bool = True,
    video_file: str = "recording.mp4",
    include_trace: bool = False,
    build_site: Callable[[Path], Path] | None = None,
    verify: Callable[[str, str | None], Any] | None = None,
    pages_url: str | None = None,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> PublishResult:
    """Publish one finished run. Order matters: audit → upload → commit → verify.

    ``build_site`` receives the worktree and returns the built ``dist`` dir (or
    None to leave the bundle alone); ``verify`` is called with
    ``(url, expected_content_type)`` for the video and one screenshot.
    """
    run_dir = Path(run_dir)
    run_id = run_dir.name
    summary_path = run_dir / "run_summary.json"
    if not summary_path.is_file():
        raise PublishError(f"{run_id}: no run_summary.json — the run has not finished (or is not a run dir)")
    projected = project_run_dir(run_dir)
    if projected is None:
        raise PublishError(f"{run_id}: run_summary.json is unreadable")
    # The public site is the benchmark: official runs that reached the end of
    # their ladder (completed) or were ended by it (terminated). A casual run is
    # not a result and a crashed/cancelled one is not a finished run — the
    # online History shows neither a kind badge nor a status column because of
    # this rule (Andreas, 2026-09-09).
    if projected.kind != RunKind.official:
        raise PublishError(f"{run_id}: kind is {projected.kind.value}; only official runs are published")
    if projected.status not in (RunStatus.completed, RunStatus.terminated):
        raise PublishError(f"{run_id}: status is {projected.status.value}; publish only completed or terminated runs")
    # One run per model + thinking level: a second run for a level that is
    # already up must wait for `pokemon unpublish <old>` (board_clash). The
    # worktree is synced here, before the uploads, so the check reads the board
    # as pushed (an orphan branch with no commit yet cannot be ensured twice).
    pages.ensure()
    other = board_clash(pages.read_board(), projected.model, run_id)
    if other:
        raise PublishError(f"{run_id}: {projected.model} is already on the board as {other} — run "
                           f'"pokemon unpublish {other}" first')

    # -- assemble the outgoing files (in memory first: audit before any write)
    from src.app.trace_build import cached_run_trace

    summary_text = public_summary_text(summary_path)
    screenshots_base = store.url(f"runs/{run_id}/screenshots")
    trace_text: str | None = None
    shot_names: list[str] = []
    if include_trace:
        trace, shot_names = rewrite_trace(cached_run_trace(run_dir), screenshots_base)
        trace_text = json.dumps(trace, indent=2, ensure_ascii=False)

    # `recording.mp4` is the run's recording — the full panel when the run
    # recorded both views, which is why the full view is the default upload;
    # `video_file="recording-simple.mp4"` picks the 1:1 file instead. The R2
    # key is always recording.mp4, so a row has one video_url shape.
    video_path = run_dir / video_file
    if include_video and video_file != "recording.mp4" and not video_path.is_file():
        raise PublishError(f"{run_id}: no {video_file} in the run dir")
    has_video = include_video and video_path.is_file()
    video_url = store.url(f"runs/{run_id}/recording.mp4") if has_video else None
    video_view = recorded_view(run_dir, video_file) if has_video else None

    row = projected.model_dump(mode="json")
    for k in ROW_PRIVATE_KEYS:
        row.pop(k, None)
    row.update({
        "has_recording": has_video,
        "video_url": video_url,
        "video_view": video_view,
        "screenshots_base_url": screenshots_base if shot_names else None,
        "trace_published": include_trace,
        "published_at": _now_iso(now),
    })
    row_text = json.dumps(row, indent=2, ensure_ascii=False)

    outgoing = {"leaderboard row": row_text, f"{run_id}/summary.json": summary_text}
    if trace_text is not None:
        outgoing[f"{run_id}/trace.json"] = trace_text
    hits = audit_files(outgoing, secrets)
    if hits:
        shown = "\n".join(f"  {h}" for h in hits[:20])
        more = f"\n  … and {len(hits) - 20} more" if len(hits) > 20 else ""
        raise PublishError(f"refusing to publish {run_id}: the leak audit hit {len(hits)} line(s):\n{shown}{more}")

    # -- screenshots must exist locally before we promise them
    shots_dir = run_dir / "screenshots"
    missing = [n for n in shot_names if not (shots_dir / n).is_file()]
    if missing:
        raise PublishError(f"{run_id}: trace references {len(missing)} screenshot(s) not on disk, e.g. {missing[0]}")

    # -- upload to R2
    keys: list[str] = []
    if has_video:
        key = f"runs/{run_id}/recording.mp4"
        log(f"uploading {video_path.stat().st_size / 1e6:.1f} MB video → {key}")
        store.put_file(video_path, key, "video/mp4")
        keys.append(key)
    if shot_names:
        log(f"uploading {len(shot_names)} screenshots")

        def _put(name: str) -> str:
            key = f"runs/{run_id}/screenshots/{name}"
            store.put_file(shots_dir / name, key, "image/png")
            return key

        with ThreadPoolExecutor(max_workers=8) as pool:
            keys.extend(pool.map(_put, shot_names))

    # -- gh-pages (worktree synced by ensure() before the guard above)
    files = {"summary.json": summary_text}
    route_text = route_json(run_dir)
    if route_text is not None:
        files["route.json"] = route_text
    else:
        # a run that lost its trace must not keep a stale route from an earlier publish
        old_route = pages.run_dir(run_id) / "route.json"
        if old_route.exists():
            old_route.unlink()
    if trace_text is not None:
        files["trace.json"] = trace_text
    else:
        # a --no-trace publish must not leave a stale trace from an earlier one
        old = pages.run_dir(run_id) / "trace.json"
        if old.exists():
            old.unlink()
    pages.write_run_files(run_id, files)
    pages.upsert_row(row)
    if benchmarks is not None:
        pages.write_benchmarks(benchmarks)
    if models is not None:
        pages.write_models(public_models(models, pages.read_board()))
    if build_site is not None:
        dist = build_site(pages.worktree)
        if dist is not None:
            pages.sync_site(Path(dist))
    committed = pages.commit_and_push(f"publish {run_id}")
    log("gh-pages: " + ("pushed" if committed else "nothing changed"))

    # -- verify what the world sees
    if verify is not None:
        if video_url:
            verify(video_url, "video/mp4")
        if shot_names:
            verify(f"{screenshots_base}/{shot_names[0]}", "image/png")

    from src.config import parse_model_alias

    page = f"{pages_url}models/{parse_model_alias(projected.model)[0]}" if pages_url else None
    return PublishResult(run_id=run_id, row=row, video_url=video_url, screenshots=shot_names,
                         page_url=page, committed=committed, r2_keys=keys)


# Keys a published row carries that the projection does not know about; kept
# verbatim when the row is re-projected.
ROW_PUBLISH_KEYS = ("has_recording", "video_url", "video_view", "screenshots_base_url", "trace_published", "published_at")


def refresh_rows(pages: PagesRepo, runs_root: Path, *, secrets: Iterable[str] = (), log: Callable[[str], None] = print) -> int:
    """Re-project every published row whose run dir is still on this machine.

    A published row is a cached ``project_run_dir()`` result plus the publish-only
    keys (video URL, published_at, ...). When the projection learns a field —
    ``gate_turns`` on 2026-09-13 — the rows on gh-pages do not have it, and the
    board reads the rows, not the run dirs. This rewrites the projected part of
    each row from the local run dir and keeps the publish-only keys; rows whose
    run dir is gone are left as they are. The rewritten rows go through the same
    leak audit as a fresh publish. Returns how many rows changed. This is its
    own verb (``pokemon publish --site-only --refresh-rows``): rebuilding the
    bundle must not silently rewrite rows, and rewriting rows must be asked for.
    """
    runs_root = Path(runs_root)
    rows = pages.read_board()
    changed = 0
    out: list[dict] = []
    for row in rows:
        run_id = row.get("run_id")
        run_dir = runs_root / str(run_id)
        projected = project_run_dir(run_dir) if run_id and (run_dir / "run_summary.json").is_file() else None
        if projected is None:
            out.append(row)
            continue
        fresh = projected.model_dump(mode="json")
        for k in ROW_PRIVATE_KEYS:
            fresh.pop(k, None)
        for k in ROW_PUBLISH_KEYS:
            if k in row:
                fresh[k] = row[k]
        if fresh != row:
            hits = audit_files({f"{run_id}.json": json.dumps(fresh, indent=2, ensure_ascii=False)}, secrets)
            if hits:
                raise PublishError(f"{run_id}: refreshed row failed the leak audit: {hits[0]}")
            changed += 1
            log(f"row refreshed: {run_id}")
        out.append(fresh)
        # The route file is a projection of the same run dir: (re)write it when
        # the run has one and the published copy is missing or stale.
        route_text = route_json(run_dir)
        if route_text is not None:
            target = pages.run_dir(str(run_id)) / "route.json"
            if not target.is_file() or target.read_text() != route_text:
                pages.write_run_files(str(run_id), {"route.json": route_text})
                log(f"route written: {run_id}")
    if changed:
        pages.write_board(out)
    return changed


def publish_site(
    *,
    pages: PagesRepo,
    build_site: Callable[[Path], Path],
    benchmarks: list[dict] | None = None,
    models: list[dict] | None = None,
    refresh: Callable[[PagesRepo], int] | None = None,
    log: Callable[[str], None] = print,
) -> bool:
    """Rebuild and push the SPA bundle (and the benchmark registry) — no run touched.

    ``refresh`` (``--refresh-rows``) runs after the worktree is synced and before
    the build, so its row rewrite lands in the same commit; it is the one caller
    allowed to change rows here, and only because the flag asked for it.

    `publish_run` is the only other thing that rebuilds the bundle, and using it
    as a build trigger re-uploads a video and REPLACES that run's row (a
    `--no-video` publish blanked a live video_url on 2026-09-08). A UI change
    gets its own verb. Returns True when something was pushed.
    """
    pages.ensure()
    refreshed = refresh(pages) if refresh is not None else 0
    if benchmarks is not None:
        pages.write_benchmarks(benchmarks)
    if models is not None:
        pages.write_models(public_models(models, pages.read_board()))
    dist = build_site(pages.worktree)
    if dist is not None:
        pages.sync_site(Path(dist))
    committed = pages.commit_and_push(f"rebuild site, refresh {refreshed} rows" if refreshed else "rebuild site")
    log("gh-pages: " + ("pushed" if committed else "nothing changed"))
    return committed


def unpublish_run(
    run_id: str,
    *,
    store: R2Store,
    pages: PagesRepo,
    models: list[dict] | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Reverse a publish: R2 prefix delete, data files gone, row removed.

    ``models`` (the registry catalog) rewrites ``data/models.json`` too, so a
    model whose last run went down leaves the catalog with it."""
    deleted = store.delete_prefix(f"runs/{run_id}/")
    log(f"R2: deleted {deleted} object(s) under runs/{run_id}/")
    pages.ensure()
    removed_files = pages.remove_run_files(run_id)
    removed_row = pages.remove_row(run_id)
    if models is not None:
        pages.write_models(public_models(models, pages.read_board()))
    committed = pages.commit_and_push(f"unpublish {run_id}")
    log("gh-pages: " + ("pushed" if committed else "nothing changed"))
    return {"r2_deleted": deleted, "row_removed": removed_row, "files_removed": removed_files,
            "committed": committed}
