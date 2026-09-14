"""`pokemon publish` — put a finished run on the online leaderboard.

  pokemon publish <run_id>                 # result row + summary → gh-pages, video → R2
  pokemon publish <run_id> --no-video      # result only
  pokemon publish <run_id> --video simple  # upload recording-simple.mp4 instead of the full-panel file
  pokemon publish <run_id> --with-trace    # ALSO the report trace + screenshots (off by default)
  pokemon publish <run_id> --dry-run       # audit + list what would go, touch nothing
  pokemon publish <run_id> --no-build      # skip the SPA rebuild (data-only push)

The public page is result and video only: the per-turn reasoning never leaves
the machine unless --with-trace says so, and the published summary is
run_summary.json without its `turns` list.

Order of operations, and why: the exact outgoing JSON is leak-audited BEFORE
anything is uploaded (every `.env` secret, key shapes, home paths — any hit
aborts naming file and line); then R2 gets the heavy files; then the `gh-pages`
worktree under `local/` gets the JSON + the static SPA build, one commit, one
push; then the video and one screenshot are fetched anonymously with a named
User-Agent (Cloudflare 403s Python's default agent on r2.dev). Settings come
from `.env` — see `.env.example`. Plan: artifacts/online-leaderboard/plan.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.app import publish as pub

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "src" / "dashboard" / "web"


def _settings_and_store() -> tuple[pub.R2Settings, pub.R2Store]:
    settings = pub.R2Settings.from_env()
    client = pub.make_r2_client(settings)
    return settings, pub.R2Store(client, settings.bucket, settings.public_base_url)


def _pages(log) -> pub.PagesRepo:
    return pub.PagesRepo(REPO_ROOT, REPO_ROOT / "local" / pub.WORKTREE_DIRNAME, log=log)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Publish a finished run to the online leaderboard (Cloudflare R2 + GitHub Pages).",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("run_id", nargs="?", help="run-dir name under --runs-root, e.g. 2026-09-08_12-04-58_config-5.1__glm-5-3-flash-high")
    ap.add_argument("--site-only", action="store_true",
                    help="rebuild and push the SPA bundle only — no run_id, nothing uploaded, no row changed (after a UI change)")
    ap.add_argument("--refresh-rows", action="store_true",
                    help="with --site-only: re-project every published row from its local run dir first (after the projection learns a field)")
    ap.add_argument("--runs-root", default=str(REPO_ROOT / "local" / "runs"), help="where run folders live (default local/runs)")
    ap.add_argument("--no-video", action="store_true", help="do not upload a recording even if present")
    ap.add_argument("--video", choices=["full", "simple"], default="full",
                    help="which file to upload when the run recorded both views: full = recording.mp4 (default), simple = recording-simple.mp4")
    ap.add_argument("--with-trace", action="store_true", help="also publish the report trace and its screenshots (default: result + video only)")
    ap.add_argument("--no-build", action="store_true", help="do not rebuild the static SPA; push data only")
    ap.add_argument("--skip-verify", action="store_true", help="do not fetch the public URLs afterwards")
    ap.add_argument("--dry-run", action="store_true", help="run the audit, print what would be published, change nothing")
    return ap


def main() -> None:
    args = _parser().parse_args()
    load_dotenv(REPO_ROOT / ".env")
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    if args.site_only:
        if args.run_id:
            sys.exit("ERROR: --site-only takes no run_id; it rebuilds the bundle and touches no run")
        _site_only(log, refresh_rows_from=Path(args.runs_root) if args.refresh_rows else None)
        return
    if args.refresh_rows:
        sys.exit("ERROR: --refresh-rows goes with --site-only")
    if not args.run_id:
        sys.exit("ERROR: a run_id is required (or --site-only to push the bundle alone)")
    run_dir = Path(args.runs_root) / args.run_id
    if not run_dir.is_dir():
        sys.exit(f"ERROR: no run dir at {run_dir}")

    secrets = pub.secret_values(pub.read_env_file(REPO_ROOT / ".env"))

    try:
        settings, store = _settings_and_store()
        pages = _pages(log)
        pages_url = pub.pages_base_url(pages.remote_url(), __import__("os").environ.get("PAGES_BASE_URL"))

        if args.dry_run:
            _dry_run(run_dir, store, pages, secrets, args)
            return

        from src.app.benchmarks import benchmarks_payload
        from src.app.catalog import model_catalog

        build = None
        if not args.no_build:
            def build(_worktree: Path) -> Path:
                return pub.build_static_site(WEB_DIR, WEB_DIR / pub.SITE_DIST_DIRNAME, pub.base_path(pages_url), log=log)

        verify = None if args.skip_verify else (lambda url, ctype: pub.verify_public_url(url, expect_type=ctype))
        result = pub.publish_run(
            run_dir, store=store, pages=pages, secrets=secrets, benchmarks=pub.public_benchmarks(benchmarks_payload()),
            models=model_catalog(), include_video=not args.no_video, include_trace=args.with_trace,
            video_file="recording-simple.mp4" if args.video == "simple" else "recording.mp4",
            build_site=build, verify=verify, pages_url=pages_url, log=log,
        )
    except pub.PublishError as exc:
        sys.exit(f"ERROR: {exc}")

    print(f"published {result.run_id}")
    print(f"  page   {result.page_url}")
    print(f"  video  {result.video_url or '(none)'}")
    print(f"  shots  {len(result.screenshots)}")
    if result.committed:
        print("  Pages picks up the push within ~1 minute.")


def _site_only(log, refresh_rows_from: Path | None = None) -> None:
    """`pokemon publish --site-only`: rebuild the SPA, push gh-pages, change no row
    — unless ``--refresh-rows`` asks for the published rows to be re-projected
    from their local run dirs first (pub.refresh_rows)."""
    from src.app.benchmarks import benchmarks_payload
    from src.app.catalog import model_catalog

    try:
        pages = _pages(log)
        pages_url = pub.pages_base_url(pages.remote_url(), __import__("os").environ.get("PAGES_BASE_URL"))
        refresh = None
        if refresh_rows_from is not None:
            secrets = pub.secret_values(pub.read_env_file(REPO_ROOT / ".env"))
            refresh = lambda p: pub.refresh_rows(p, refresh_rows_from, secrets=secrets, log=log)  # noqa: E731
        build = lambda _wt: pub.build_static_site(WEB_DIR, WEB_DIR / pub.SITE_DIST_DIRNAME, pub.base_path(pages_url), log=log)  # noqa: E731
        committed = pub.publish_site(pages=pages, build_site=build, benchmarks=pub.public_benchmarks(benchmarks_payload()),
                                     models=model_catalog(), refresh=refresh, log=log)
    except pub.PublishError as exc:
        sys.exit(f"ERROR: {exc}")
    print(f"site {'rebuilt and pushed' if committed else 'unchanged'}  {pages_url}")
    if committed:
        print("  Pages picks up the push within ~1 minute.")


def _dry_run(run_dir: Path, store: pub.R2Store, pages: pub.PagesRepo, secrets: list[str], args) -> None:
    """Everything publish_run checks before its first side effect, then stop."""
    import json
    from src.app.projection import project_run_dir
    from src.app.trace_build import cached_run_trace

    projected = project_run_dir(run_dir)
    if projected is None:
        raise pub.PublishError(f"{run_dir.name}: no readable run_summary.json")
    pages.ensure()
    clash = pub.board_clash(pages.read_board(), projected.model, run_dir.name)
    files = {"summary.json": pub.public_summary_text(run_dir / "run_summary.json")}
    shots: list[str] = []
    if args.with_trace:
        trace, shots = pub.rewrite_trace(cached_run_trace(run_dir), store.url(f"runs/{run_dir.name}/screenshots"))
        files["trace.json"] = json.dumps(trace, indent=2, ensure_ascii=False)
    hits = pub.audit_files(files, secrets)
    video = run_dir / ("recording-simple.mp4" if args.video == "simple" else "recording.mp4")
    print(f"dry run for {run_dir.name} (status {projected.status.value})")
    if clash:
        print(f"  BOARD: {projected.model} is already on the board as {clash} — publish would refuse; "
              f'run "pokemon unpublish {clash}" first')
    print(f"  video        {'yes, %.1f MB' % (video.stat().st_size / 1e6) if video.is_file() and not args.no_video else 'no'}")
    print(f"  trace        {'yes, %d screenshots' % len(shots) if args.with_trace else 'no (result + video only)'}")
    print(f"  json bytes   {sum(len(t) for t in files.values()):,}")
    if hits:
        print(f"  AUDIT: {len(hits)} hit(s) — publish would refuse:")
        for h in hits[:20]:
            print(f"    {h}")
    else:
        print("  audit        clean")


if __name__ == "__main__":
    main()
