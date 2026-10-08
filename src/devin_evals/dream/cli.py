"""devin-dream CLI: unit | inject | fleet."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from devin_evals.dream.defects import ADVERSARIAL, DEFECTS, UNIT_IDS
from devin_evals.dream.generate import (
    build_fleet,
    write_expected,
    write_fleet_manifest,
    write_sessions_db,
    write_state_vscdb,
)


def _cmd_unit(args: argparse.Namespace) -> int:
    ids = list(UNIT_IDS) if args.defect == ["all"] else args.defect
    unknown = [d for d in ids if d not in DEFECTS]
    if unknown:
        print(f"error: unknown defect(s): {', '.join(unknown)}",
              file=sys.stderr)
        return 2
    out = Path(args.out)
    for did in ids:
        spec = DEFECTS[did]()
        ddir = out / did.lower()
        write_sessions_db(ddir / "sessions.db", [spec],
                          schema_version=args.schema_version, seed=args.seed)
        ep = write_expected(ddir, spec)
        print(f"{did}: {ddir / 'sessions.db'} (+ {ep.name})")
    return 0


def _cmd_inject(args: argparse.Namespace) -> int:
    """Adversarial sessions (D07/D08) + the expected block/pass scorecard."""
    if args.n < 1:
        print("inject: --n must be >= 1", file=sys.stderr)
        return 2
    out = Path(args.out)
    specs = [DEFECTS[d]() for d in ADVERSARIAL for _ in range(args.n)]
    # unique session ids per copy
    seen: dict[str, int] = {}
    final = []
    for s in specs:
        seen[s.session_id] = seen.get(s.session_id, 0) + 1
        suffix = seen[s.session_id]
        import dataclasses
        final.append(dataclasses.replace(
            s, session_id=f"{s.session_id}-{suffix:03d}"))
    write_sessions_db(out / "sessions.db", final, seed=args.seed)
    scorecard = {
        "mode": "inject",
        "sessions": len(final),
        "expected_blocks": {d: args.n for d in ADVERSARIAL},
        "note": "each session must be denied/quarantined by the target "
                "tool; a pass is a hole in the policy",
    }
    (out / "expected.json").write_text(
        json.dumps(scorecard, indent=2) + "\n", encoding="utf-8")
    for s in final:
        write_expected(out / s.session_id, s)
    print(f"inject: {len(final)} adversarial session(s) -> "
          f"{out / 'sessions.db'}")
    return 0


def _cmd_fleet(args: argparse.Namespace) -> int:
    if args.sessions < 1:
        print("error: --sessions must be >= 1", file=sys.stderr)
        return 2
    out = Path(args.out)
    if args.vscdb is not None and (
        Path(args.vscdb).resolve() == (out / "fleet.json").resolve()
    ):
        print("fleet: --vscdb may not target the reserved fleet.json "
              "manifest path", file=sys.stderr)
        return 2
    plan = build_fleet(args.sessions, seed=args.seed)
    write_sessions_db(out / "sessions.db", plan.specs, seed=args.seed)
    counts: dict[str, int] = {}
    for s in plan.specs:
        arch = next((l.split(":", 1)[1] for l in s.labels
                     if l.startswith("archetype:")), "unknown")
        counts[arch] = counts.get(arch, 0) + 1
    mix = ", ".join(f"{a}={c}" for a, c in sorted(counts.items()))
    print(f"fleet: {len(plan.specs)} sessions ({mix}) -> "
          f"{out / 'sessions.db'}")
    if args.vscdb:
        vdb = write_state_vscdb(args.vscdb, plan, seed=args.seed)
        print(f"fleet: {len(plan.gui)} GUI binding(s) -> {vdb}")
    if args.manifest:
        mp = write_fleet_manifest(out, plan, seed=args.seed)
        print(f"fleet: manifest -> {mp}")
    else:
        # a stale fleet.json must not outlive the sessions it describes
        (out / "fleet.json").unlink(missing_ok=True)
    return 0


def add_dream_subcommands(sub) -> None:
    """Register unit/inject/fleet on an argparse subparsers collection."""

    u = sub.add_parser("unit", help="one sessions.db + expected.json per defect")
    u.add_argument("--out", required=True)
    u.add_argument("--defect", nargs="+", default=["all"],
                   help="defect ids or 'all' (unit covers D01-D06 + D09-D10)")
    u.add_argument("--schema-version", type=int, default=None)
    u.add_argument("--seed", type=int, default=0xDEE4)
    u.set_defaults(func=_cmd_unit)

    i = sub.add_parser("inject",
                       help="adversarial sessions + expected scorecard")
    i.add_argument("--out", required=True)
    i.add_argument("--n", type=int, default=1,
                   help="copies per adversarial defect (D07, D08)")
    i.add_argument("--seed", type=int, default=0xDEE4)
    i.set_defaults(func=_cmd_inject)

    f = sub.add_parser("fleet",
                       help="seeded population of archetype sessions")
    f.add_argument("--out", required=True)
    f.add_argument("--sessions", "--n", dest="sessions", type=int,
                   default=1000,
                   help="population size (--n kept as an alias)")
    f.add_argument("--seed", type=int, default=0)
    f.add_argument("--vscdb", default=None, metavar="PATH",
                   help="also emit a synthetic state.vscdb at PATH with "
                        "windsurfSpace.sessionWorkspace bindings for a "
                        "fraction of sessions")
    f.add_argument("--manifest", action="store_true",
                   help="write fleet.json with per-session archetype and "
                        "expected properties")
    f.set_defaults(func=_cmd_fleet)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devin-evals dream",
        description="Synthetic Devin sessions with known verdicts — "
        "regression and adversarial test data for the catalog.")
    add_dream_subcommands(p.add_subparsers(dest="command", required=True))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
