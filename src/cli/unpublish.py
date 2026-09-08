"""`pokemon unpublish <run_id>` — reverse `pokemon publish`.

Deletes the run's objects from R2, removes its JSON and its leaderboard row
from the `gh-pages` branch, commits and pushes. Local files are untouched.
"""

from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv

from src.app import publish as pub
from src.cli.publish import REPO_ROOT, _pages, _settings_and_store


def main() -> None:
    ap = argparse.ArgumentParser(description="Take a published run off the online leaderboard.")
    ap.add_argument("run_id")
    args = ap.parse_args()
    load_dotenv(REPO_ROOT / ".env")
    log = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    try:
        _settings, store = _settings_and_store()
        result = pub.unpublish_run(args.run_id, store=store, pages=_pages(log), log=log)
    except pub.PublishError as exc:
        sys.exit(f"ERROR: {exc}")
    print(f"unpublished {args.run_id}: {result['r2_deleted']} R2 object(s), row "
          f"{'removed' if result['row_removed'] else 'was not on the board'}")


if __name__ == "__main__":
    main()
