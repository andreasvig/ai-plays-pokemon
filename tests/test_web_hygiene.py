"""Dead code in the Svelte tree, which nothing else notices.

``InteriorPopup.svelte`` was orphaned by the per-game atlas seam (5f03077) when
the building view was inlined into ``RouteMap``, and then kept in sync BY HAND
through two later commits — 9.6 KB of a component that no bundle ever pulled
in. Nothing failed, because an unimported component is not a build error; it
is only waste, and waste that reads as live code to the next person editing it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB_SRC = REPO / "src" / "dashboard" / "web" / "src"

# The bundle's own entry point: imported by main.js, not by another component.
ROOTS = {"App.svelte"}

# Orphans that predate this test, named rather than papered over. Both are
# queue/gate surfaces, nothing to do with the map work that found them, so
# they are left for whoever owns that UI to delete or wire. A NEW orphan still
# fails; these two are the population as measured on 2026-09-21, not a pattern.
KNOWN_ORPHANS = {"components/GateBar.svelte", "components/QueuePanel.svelte"}


def test_every_svelte_component_is_imported_somewhere():
    components = sorted(p for p in WEB_SRC.rglob("*.svelte") if p.name not in ROOTS)
    assert components, "no components found — the path is wrong, not the tree empty"

    # One read of the whole tree; a component may be imported by a sibling
    # component, by App.svelte, or by a plain .js module (a lazy route).
    sources = {p: p.read_text() for p in
               list(WEB_SRC.rglob("*.svelte")) + list(WEB_SRC.rglob("*.js"))}

    orphans = []
    for comp in components:
        stem = comp.stem
        # `import Foo from './Foo.svelte'` and the default-plus-named form
        # `import Foo, { bar } from './Foo.svelte'` — TraceFeed uses the
        # second one for Action, and a regex missing it invents an orphan.
        pattern = re.compile(
            rf"import\s+{stem}\s*(?:,\s*\{{[^}}]*\}}\s*)?from\s+['\"][^'\"]*{stem}\.svelte['\"]")
        if not any(pattern.search(text) for path, text in sources.items() if path != comp):
            orphans.append(str(comp.relative_to(WEB_SRC)))

    new = sorted(set(orphans) - KNOWN_ORPHANS)
    assert not new, (
        "these components are in the tree but nothing imports them — delete "
        f"them or wire them up: {new}")

    # The keep-list is itself a claim: if one of those gets wired or deleted,
    # this fails and the list shrinks, rather than quietly excusing nothing.
    assert set(orphans) == KNOWN_ORPHANS, (
        f"the known-orphan list is stale: still orphaned {sorted(orphans)}")
