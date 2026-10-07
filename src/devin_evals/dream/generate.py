"""Write labeled synthetic sessions into a real-shape ``sessions.db``.

Reuses the verified DDL from ``devin_internals.fixtures`` (the migration
ledger + table layout), but inserts dream content: realistic messages,
tool calls, and the per-defect ``expected.json`` verdict file.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from devin_internals.fixtures import (
    _BASE_TS_MS,
    _FUTURE_MIGRATION_DATE,
    _MIGRATION_APPLIED_ON,
    SESSIONS_DB_DDL_V17,
    STATE_VSCDB_DDL,
    LATEST_KNOWN_SCHEMA,
)

from devin_evals.dream.defects import SessionSpec, ToolCallSpec, _exec_call

DREAM_SEED_TAG = "devin-dream"


def _checksum(seed: int, version: int) -> str:
    return hashlib.sha256(
        f"{DREAM_SEED_TAG}:{seed}:{version}".encode()).hexdigest()


def write_sessions_db(
    path: str | Path,
    specs: list[SessionSpec],
    *,
    schema_version: int | None = None,
    seed: int = 0xDEE4,
) -> Path:
    """Create a ``sessions.db`` holding the given session specs.

    ``schema_version`` defaults to the latest known (17); a spec with
    ``schema_version_override`` forces the whole DB to that version (D06
    uses it — one drifted DB per defect dir).
    """
    path = Path(path)
    overrides = {
        s.schema_version_override for s in specs
        if s.schema_version_override is not None
    }
    if len(overrides) > 1:
        raise ValueError(
            f"conflicting schema_version_override values: {sorted(overrides)}")
    if overrides:
        schema_version = overrides.pop()
    version = schema_version or LATEST_KNOWN_SCHEMA

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    con = sqlite3.connect(path)
    with con:
        con.executescript(SESSIONS_DB_DDL_V17)
        for v in range(1, version + 1):
            con.execute(
                "INSERT INTO refinery_schema_history(version, name,"
                " applied_on, checksum) VALUES (?, ?, ?, ?)",
                (v, f"dream_migration_{v:02d}",
                 _MIGRATION_APPLIED_ON.get(v, _FUTURE_MIGRATION_DATE),
                 _checksum(seed, v)),
            )
        con.execute(
            "INSERT INTO app_state(key, value) VALUES (?, ?)",
            ("schema_compat_version", str(version)))
        for i, spec in enumerate(specs):
            _insert_spec(con, spec, i)
    con.close()
    return path


def _insert_spec(con: sqlite3.Connection, spec: SessionSpec, i: int) -> None:
    created = _BASE_TS_MS + i * 3_600_000
    dirs = list(spec.workspace_dirs) or [spec.working_directory]
    con.execute(
        "INSERT INTO sessions(id, working_directory, backend_type, model,"
        " agent_mode, created_at, last_activity_at, title, main_chain_id,"
        " shell_last_seen_index, cogs_json, workspace_dirs, hidden, metadata)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            spec.session_id,
            spec.working_directory,
            "dream-backend",
            spec.model,
            spec.agent_mode,
            created,
            created + spec.duration_ms,
            spec.title,
            1,
            0,
            json.dumps({"synthetic": True}),
            json.dumps(dirs),
            0,
            json.dumps({"synthetic": True, "labels": list(spec.labels),
                        "defect": spec.defect_id}),
        ),
    )
    for node_id, (role, blob) in enumerate(spec.messages, start=1):
        con.execute(
            "INSERT INTO message_nodes(session_id, node_id, parent_node_id,"
            " chat_message, created_at, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (spec.session_id, node_id, None if node_id == 1 else node_id - 1,
             blob, created + node_id * 10_000,
             json.dumps({"synthetic": True})),
        )
    for tc in spec.tool_calls:
        con.execute(
            "INSERT INTO tool_call_state(session_id, tool_call_id,"
            " tool_call_json, tool_call_update_json) VALUES (?, ?, ?, ?)",
            (spec.session_id, tc.tool_call_id, json.dumps(tc.call),
             json.dumps(tc.update) if tc.update is not None else None),
        )


def write_expected(out_dir: str | Path, spec: SessionSpec) -> Path:
    """``expected.json`` next to the generated DB — the known verdicts."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    p = Path(out_dir) / "expected.json"
    p.write_text(
        json.dumps({
            "defect": spec.defect_id,
            "session_id": spec.session_id,
            "title": spec.title,
            "labels": list(spec.labels),
            "expected": spec.expected,
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# fleet mode — seeded archetype population + manifest + optional state.vscdb
# ---------------------------------------------------------------------------

_FLEET_MODELS = ("swe-1.6", "swe-1.5", "gpt-5-codex", "claude-sonnet")
_FLEET_MODES = ("interactive", "plan", "exec")

# Roadmap F5 archetypes. Every archetype is guaranteed to appear once in the
# first ``len(_ARCHETYPES)`` sessions (shuffled by seed); the remainder is
# drawn from these weights.
ARCHETYPES = (
    "short-task",
    "long-refactor",
    "failed-attempt",
    "multi-project",
    "heavy-churn",
    "commit-producing",
    "empty-trivial",
)
_ARCH_WEIGHTS = (30, 12, 12, 10, 12, 14, 10)  # ~%, aligned with ARCHETYPES

# state.vscdb shape — mirrors what devin-graph/devin-history parse (GR-1/HI-1)
SESSION_WS_PREFIX = "windsurfSpace.sessionWorkspace/"
RESOURCE_TO_SPACE = "windsurfSpace.resourceToSpace"
SPACE_METADATA = "windsurfSpace.metadata"
_EDITOR_URI_PREFIX = "vscode-cascade-editor:///cascade-acp/"
_GUI_BACKEND = "acp/dream-cli"
_GUI_FRACTION = 0.35  # share of sessions that also get a GUI binding

_SLUG_ADJ = (
    "amber", "brisk", "canyon", "drift", "ember", "frost", "harbor",
    "ivory", "lunar", "maple", "onyx", "quartz", "solar", "tidal",
    "velvet", "willow",
)
_SLUG_NOUN = (
    "anchor", "atlas", "beacon", "compass", "falcon", "harvest",
    "lantern", "meadow", "newspaper", "prairie", "riddle", "signal",
    "tundra", "vector", "violin", "zephyr",
)


@dataclass(frozen=True)
class GuiBinding:
    """One ``windsurfSpace.sessionWorkspace`` entry for a fleet session."""

    session_id: str
    slug: str
    backend: str
    workspace: str
    space_id: str
    last_accessed: int


@dataclass(frozen=True)
class FleetPlan:
    """Everything ``fleet`` produces: session specs + GUI bindings."""

    specs: list[SessionSpec]
    gui: dict[str, GuiBinding]  # session_id -> binding (subset of specs)


def _fleet_base(rng: random.Random, arch: str, i: int, title: str,
                working_directory: str | None = None) -> dict[str, Any]:
    return {
        "defect_id": "FLEET",
        "session_id": f"dream-fleet-{i:05d}",
        "title": title,
        "working_directory": (
            working_directory or f"/dream/fleet/proj-{rng.randint(0, 9)}"),
        "model": rng.choice(_FLEET_MODELS),
        "agent_mode": rng.choice(_FLEET_MODES),
        "labels": ("synthetic", f"archetype:{arch}"),
    }


def _msgs(pairs: list[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        _msg(role, text, j) for j, (role, text) in enumerate(pairs, start=1)
    )


def _short_task(rng: random.Random, sid: str, i: int) -> SessionSpec:
    n_msgs = rng.randint(2, 6)
    pairs = [
        ("user" if j % 2 else "assistant",
         f"small fix, step {j}")
        for j in range(1, n_msgs + 1)
    ]
    tcs = tuple(
        _exec_call(f"call_{sid}_{t}",
                   rng.choice(("ls src", "pytest -x", "ruff check .")),
                   "ok")
        for t in range(rng.randint(1, 3))
    )
    return SessionSpec(
        **_fleet_base(rng, "short-task", i, f"Short task {i}"),
        messages=_msgs(pairs),
        tool_calls=tcs,
        expected={"message_count": n_msgs, "tool_call_count": len(tcs)},
    )


def _long_refactor(rng: random.Random, sid: str, i: int) -> SessionSpec:
    n_msgs = rng.randint(20, 40)
    pairs = [
        ("user" if j % 2 else "assistant",
         f"refactor iteration {j}")
        for j in range(1, n_msgs + 1)
    ]
    tcs = tuple(
        _exec_call(f"call_{sid}_{t}",
                   rng.choice(("grep -rn TODO src", "pytest",
                               "sed -i 's/old/new/' src/core.py")),
                   "ok")
        for t in range(rng.randint(6, 14))
    )
    duration = rng.randint(1_800_000, 7_200_000)  # 30min - 2h
    return SessionSpec(
        **_fleet_base(rng, "long-refactor", i, f"Long refactor {i}"),
        messages=_msgs(pairs),
        tool_calls=tcs,
        duration_ms=duration,
        expected={"long_running": True, "duration_ms": duration,
                  "message_count": n_msgs, "tool_call_count": len(tcs)},
    )


def _failed_attempt(rng: random.Random, sid: str, i: int) -> SessionSpec:
    attempts = rng.randint(2, 5)
    pairs = [("user", f"attempt to fix the build, try {a}")
             for a in range(1, attempts + 1)]
    pairs.append(("assistant",
                  "The suite still fails after the last attempt — "
                  "leaving notes instead of forcing it."))
    tcs = tuple(
        _exec_call(f"call_{sid}_{t}", "pytest -x",
                   f"FAILED test_core.py::case_{t}", exit_code=1)
        for t in range(attempts)
    )
    return SessionSpec(
        **_fleet_base(rng, "failed-attempt", i, f"Failed attempt {i}"),
        messages=_msgs(pairs),
        tool_calls=tcs,
        expected={"failed": True, "exit_code": 1,
                  "message_count": len(pairs), "tool_call_count": len(tcs)},
    )


def _multi_project(rng: random.Random, sid: str, i: int) -> SessionSpec:
    dirs = tuple(
        f"/dream/fleet/proj-{p}"
        for p in rng.sample(range(10), k=rng.randint(2, 3))
    )
    pairs = [("user", f"touch project {d}") for d in dirs]
    pairs.append(("assistant", "Coordinated the change across projects."))
    tcs = tuple(
        _exec_call(f"call_{sid}_{t}", f"cd {d} && pytest", "ok")
        for t, d in enumerate(dirs)
    )
    return SessionSpec(
        **_fleet_base(rng, "multi-project", i, f"Multi-project {i}",
                      working_directory=dirs[0]),
        messages=_msgs(pairs),
        tool_calls=tcs,
        workspace_dirs=dirs,
        expected={"projects": list(dirs), "project_count": len(dirs),
                  "message_count": len(pairs), "tool_call_count": len(tcs)},
    )


def _heavy_churn(rng: random.Random, sid: str, i: int) -> SessionSpec:
    churned = f"src/fleet/churned_{i:05d}.py"
    churn = rng.randint(8, 14)

    def _cmd_for(t: int) -> str:
        return (
            f"sed -i 's/v{t}/v{t + 1}/' {churned}",
            f"cat {churned}",
            f"python -m rewrite {churned}",
            f"pytest tests/test_churn_{i:05d}.py",
        )[t % 4]

    tcs = tuple(
        _exec_call(f"call_{sid}_{t}", _cmd_for(t),
                   f"touch {churned} rev {t}")
        for t in range(churn)
    )
    pairs = [("user", "iterate on the same file until it converges"),
             ("assistant", f"Touched {churned} {churn} times.")]
    return SessionSpec(
        **_fleet_base(rng, "heavy-churn", i, f"Heavy churn {i}"),
        messages=_msgs(pairs),
        tool_calls=tcs,
        expected={"heavy_churn": True, "churned_file": churned,
                  "churn_count": churn,
                  "message_count": len(pairs), "tool_call_count": len(tcs)},
    )


def _commit_producing(rng: random.Random, sid: str, i: int) -> SessionSpec:
    proj = rng.randint(0, 9)
    n_commits = rng.randint(1, 2)
    shas = [f"{rng.getrandbits(160):040x}" for _ in range(n_commits)]
    tcs: list[ToolCallSpec] = [
        _exec_call(f"call_{sid}_status", "git status --short",
                   " M src/app.py"),
        _exec_call(f"call_{sid}_add", "git add src/app.py", ""),
    ]
    for k, sha in enumerate(shas):
        tcs.append(_exec_call(
            f"call_{sid}_commit_{k}",
            f"git commit -m 'fleet commit {k}'",
            f"[main {sha[:7]}] fleet commit {k}\n 1 file changed"))
        tcs.append(_exec_call(
            f"call_{sid}_revparse_{k}", "git rev-parse HEAD", sha))
    # a /commit/<sha> URL so commit_references also captures repo context
    tcs.append(_exec_call(
        f"call_{sid}_url", "git log -1 --format='%H %s'",
        f"{shas[0]} fleet commit 0\n"
        f"https://github.com/dream-org/proj-{proj}/commit/{shas[0]}"))
    pairs = [("user", "commit the change"),
             ("assistant", f"Committed {len(shas)} commit(s).")]
    return SessionSpec(
        **_fleet_base(rng, "commit-producing", i, f"Commit-producing {i}",
                      working_directory=f"/dream/fleet/proj-{proj}"),
        messages=_msgs(pairs),
        tool_calls=tuple(tcs),
        expected={"produces_commit": True, "commit_shas": shas,
                  "message_count": len(pairs), "tool_call_count": len(tcs)},
    )


def _empty_trivial(rng: random.Random, sid: str, i: int) -> SessionSpec:
    n_msgs = rng.randint(1, 2)
    pairs = [("user", "quick question"),
             ("assistant", "Nothing to change.")][:n_msgs]
    return SessionSpec(
        **_fleet_base(rng, "empty-trivial", i, f"Trivial session {i}"),
        messages=_msgs(pairs),
        tool_calls=(),
        expected={"trivial": True, "message_count": len(pairs),
                  "tool_call_count": 0},
    )


_BUILDERS = {
    "short-task": _short_task,
    "long-refactor": _long_refactor,
    "failed-attempt": _failed_attempt,
    "multi-project": _multi_project,
    "heavy-churn": _heavy_churn,
    "commit-producing": _commit_producing,
    "empty-trivial": _empty_trivial,
}


def build_fleet(n: int, *, seed: int = 0) -> FleetPlan:
    """Deterministic fleet: every archetype covered in the first 7 sessions,
    the rest weighted; ~35% of sessions also get a GUI binding."""
    rng = random.Random(seed)
    cycle = list(ARCHETYPES)
    rng.shuffle(cycle)
    specs: list[SessionSpec] = []
    for i in range(n):
        arch = (cycle[i] if i < len(cycle)
                else rng.choices(ARCHETYPES, weights=_ARCH_WEIGHTS)[0])
        specs.append(_BUILDERS[arch](rng, f"dream-fleet-{i:05d}", i))

    gui: dict[str, GuiBinding] = {}
    used_slugs: set[str] = set()
    space_of: dict[str, str] = {}
    for spec in specs:
        if rng.random() >= _GUI_FRACTION:
            continue
        slug = f"{rng.choice(_SLUG_ADJ)}-{rng.choice(_SLUG_NOUN)}"
        if slug in used_slugs:
            slug = f"{slug}-{len(used_slugs):02d}"
        used_slugs.add(slug)
        ws = spec.working_directory
        space = space_of.setdefault(ws, f"dream-space-{len(space_of):03d}")
        gui[spec.session_id] = GuiBinding(
            session_id=spec.session_id,
            slug=slug,
            backend=_GUI_BACKEND,
            workspace=ws,
            space_id=space,
            last_accessed=_BASE_TS_MS + rng.randint(0, 86_400_000),
        )
    return FleetPlan(specs=specs, gui=gui)


def fleet_specs(n: int, *, seed: int = 0) -> list[SessionSpec]:
    """Compat wrapper: just the specs (callers that don't need the plan)."""
    return build_fleet(n, seed=seed).specs


def write_state_vscdb(path: str | Path, plan: FleetPlan, *,
                      seed: int = 0) -> Path:
    """Emit a synthetic ``state.vscdb`` covering ``plan.gui`` bindings.

    Keys match what devin-graph/devin-history parse:
    ``windsurfSpace.sessionWorkspace/<backend>/<slug>`` plus the
    ``resourceToSpace``/``metadata`` rollups so ``space_id`` and
    ``lastAccessed`` resolve too.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    rts: dict[str, list[str]] = {}
    meta: dict[str, dict[str, int]] = {}
    con = sqlite3.connect(path)
    with con:
        con.executescript(STATE_VSCDB_DDL)
        for b in plan.gui.values():
            con.execute(
                "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
                (
                    f"{SESSION_WS_PREFIX}{b.backend}/{b.slug}",
                    json.dumps({
                        "workspaceId": b.workspace,
                        "label": Path(b.workspace).name,
                        "folders": [b.workspace],
                        "lastUpdated": b.last_accessed,
                        "synthetic": True,
                    }),
                ),
            )
            rts.setdefault(b.space_id, []).append(
                f"{_EDITOR_URI_PREFIX}{b.backend}/{b.slug}")
            m = meta.setdefault(b.space_id, {"lastAccessed": 0})
            m["lastAccessed"] = max(m["lastAccessed"], b.last_accessed)
        con.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            (RESOURCE_TO_SPACE, json.dumps(rts)))
        con.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            (SPACE_METADATA, json.dumps(meta)))
        con.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            ("windsurfSpace.dream.windowState",
             json.dumps({"synthetic": True, "seed": seed})))
        con.execute(
            "INSERT INTO ItemTable(key, value) VALUES (?, ?)",
            ("dream.unrelated.key", json.dumps({"synthetic": True})))
    con.close()
    return path


def write_fleet_manifest(out_dir: str | Path, plan: FleetPlan, *,
                         seed: int = 0) -> Path:
    """``fleet.json``: per-session archetype + expected properties."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    entries = []
    for s in plan.specs:
        arch = next(
            (l.split(":", 1)[1] for l in s.labels
             if l.startswith("archetype:")), "unknown")
        counts[arch] = counts.get(arch, 0) + 1
        expected = {
            # uniform flags — present on every session for easy assertions
            "produces_commit": False,
            "heavy_churn": False,
            "failed": False,
            "trivial": False,
            **s.expected,
        }
        entry = {
            "session_id": s.session_id,
            "archetype": arch,
            "title": s.title,
            "expected": expected,
        }
        binding = plan.gui.get(s.session_id)
        if binding:
            entry["gui_slug"] = binding.slug
            entry["gui_backend"] = binding.backend
        entries.append(entry)
    manifest = {
        "generator": "devin-dream fleet",
        "seed": seed,
        "session_count": len(plan.specs),
        "archetype_counts": dict(sorted(counts.items())),
        "gui_bindings": len(plan.gui),
        "sessions": entries,
    }
    p = out_dir / "fleet.json"
    p.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return p


def _msg(role: str, content: str, n: int) -> tuple[str, str]:
    return (role, json.dumps({
        "message_id": f"dream-fleet-{role}-{n:05d}",
        "role": role,
        "content": content,
        "metadata": {},
    }))
