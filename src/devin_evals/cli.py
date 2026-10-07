"""Thin CLI wrapper — all logic lives in the library modules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from devin_internals.schema import SchemaError

from devin_evals import __version__
from devin_evals.cases import CaseError, builtin_packs, load_cases
from devin_evals.corpus import generate_corpus, verify_corpus
from devin_evals.runner import run_evals


def _cmd_abrun(args: argparse.Namespace) -> int:
    """Dispatch: ``--task`` keeps the v1 two-session quick mode; the new
    default is the G3 suite harness (``--tasks``/``<evals>/tasks``)."""
    if args.task is not None:
        return _cmd_abrun_v1(args)
    return _cmd_abrun_v2(args)


def _cmd_abrun_v1(args: argparse.Namespace) -> int:
    """EV-5 (deprecated simple mode): two fresh sessions, grade both."""
    import json as _json
    from devin_evals.abrun import run_ab
    from devin_evals.runner import evaluate_case
    from devin_internals.parsers.sessions import SessionsStore
    from devin_evals.cases import load_cases
    from devin_evals.judge import judge_available
    if not args.repo:
        print("error: --repo is required for --task (simple mode)",
              file=sys.stderr)
        return _USAGE
    if args.dry_run:
        print(_json.dumps({
            "dry_run": True, "task": args.task,
            "variant_a": args.variant_a, "variant_b": args.variant_b,
            "would_create": [f"ab-run:{args.tag}:a", f"ab-run:{args.tag}:b"],
            "note": "real run consumes tokens; sessions are labelled for "
                    "janitor",
        }, indent=2))
        return _OK
    ok, msg = judge_available()
    if not ok:
        print(f"error: {msg}", file=sys.stderr)
        return _USAGE
    res = run_ab(args.task, args.variant_a, args.variant_b,
                 repo=args.repo, tag=args.tag, timeout_s=args.timeout)
    print(_json.dumps(res, indent=2))
    if not res.get("ok"):
        return _FAILED
    # grade both sessions against the case rubric
    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    case = next((c for c in cases if c.id == args.case), cases[0] if cases else None)
    if case is None:
        print("error: no eval case to grade against", file=sys.stderr)
        return _USAGE
    scores = {}
    with SessionsStore(args.sessions_db) as store:
        for variant, r in res["variants"].items():
            sid = r.get("session_id")
            if not sid:
                scores[variant] = {"error": "no session id captured"}
                continue
            outcome = evaluate_case(case.__class__(**{**case.__dict__,
                                                     "session_ref": sid}),
                                    store)
            scores[variant] = {"session_id": sid,
                               "status": outcome["status"],
                               "score": outcome["score"]}
    print(_json.dumps({"case": case.id, "scores": scores}, indent=2))
    return _OK


def _default_sessions_db() -> Path | None:
    """Autodetect Devin's sessions.db (Linux/macOS XDG, Windows APPDATA)."""
    import os
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        cand = Path(base) / "devin" / "cli" / "sessions.db" if base else None
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(
            Path.home() / ".local" / "share")
        cand = Path(base) / "devin" / "cli" / "sessions.db"
    return cand if cand is not None and cand.is_file() else None


def _make_g3_grader(cases, db_path, packs_dir):
    """Grader closure for run_ab_v2: rubric checks + secondary metrics.

    Task id → eval case when one exists in --evals; otherwise the task's
    ``kind`` selects the matching built-in/custom rubric pack. Opens the
    store per attempt so sessions created mid-run are visible.
    """
    from dataclasses import replace
    from devin_evals.cases import EvalCase, _pack_checks
    from devin_evals.runner import evaluate_case
    from devin_internals.parsers.sessions import SessionsStore

    by_id = {c.id: c for c in cases}
    pd = Path(packs_dir) if packs_dir else None

    def grade(unit, res):
        sid = res.get("session_id")
        if not sid:
            return {"success": False, "error": True,
                    "detail": "no session id captured"}
        base = by_id.get(unit.task.id)
        if base is not None:
            case = replace(base, session_ref=sid)
        else:
            checks = _pack_checks(unit.task.kind, Path("<g3>"), pd)
            case = EvalCase(
                id=unit.task.id, description=unit.task.prompt[:80],
                session_ref=sid, prompt_context=None,
                checks=tuple(checks), source=f"pack:{unit.task.kind}")
        with SessionsStore(db_path) as store:
            outcome = evaluate_case(case, store)
            tool_calls = duration_s = None
            for s in store.sessions():
                if s.id == sid:
                    tool_calls = len(store.tool_call_state(sid))
                    if s.created_at and s.last_activity_at:
                        duration_s = (s.last_activity_at - s.created_at) / 1000
                    break
        return {
            "success": outcome["status"] == "pass",
            "error": outcome["status"] == "error",
            "tool_calls": tool_calls,
            "duration_s": duration_s,
        }
    return grade


def _cmd_abrun_v2(args: argparse.Namespace) -> int:
    """G3: preregistered A/B suite — tasks manifest, k attempts, stats."""
    import json as _json
    import tempfile
    import time
    from devin_evals import abrun
    from devin_evals.judge import judge_available

    if not args.evals:
        print("error: --evals is required for the G3 suite mode "
              "(rubric grading)", file=sys.stderr)
        return _USAGE
    if args.tasks:
        tasks_dir = Path(args.tasks)
    else:
        # default: <evals>/tasks, falling back to ./tasks (the shipped pack)
        tasks_dir = Path(args.evals) / "tasks"
        if not tasks_dir.is_dir() and Path("tasks").is_dir():
            tasks_dir = Path("tasks")
    try:
        tasks = abrun.load_tasks(tasks_dir)
        trig, ctrl = abrun.validate_suite(tasks)
        units = abrun.plan_units(tasks, args.attempts, args.seed)
    except abrun.G3Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE

    caps = {"max_sessions": args.max_sessions,
            "max_total_time": args.max_total_time,
            "session_timeout": args.session_timeout}
    plan = {
        "tasks_dir": str(tasks_dir),
        "tasks": len(tasks), "trigger": trig, "control": ctrl,
        "attempts_per_arm": args.attempts,
        "total_sessions": len(units),
        "seed": args.seed, "aa": bool(args.aa),
        "caps": caps,
        "order": [f"{u.task.id}:{u.variant}:{u.attempt}" for u in units],
    }
    if args.dry_run:
        print(_json.dumps({"dry_run": True, "plan": plan,
                           "note": "real run consumes tokens; sessions are "
                                   "labelled g3-ab:<task>:<variant>:<attempt>"},
                          indent=2))
        return _OK

    over_cap = (args.max_sessions is not None
                and len(units) > args.max_sessions)
    if over_cap and not args.yes:
        if args.confirm:
            try:
                ans = input(
                    f"plan needs {len(units)} sessions > --max-sessions "
                    f"{args.max_sessions}; the run will abort at the cap. "
                    "proceed? [y/N] ")
            except EOFError:
                ans = ""
            if ans.strip().lower() != "y":
                print("aborted by user")
                return _USAGE
        else:
            print(
                f"error: plan needs {len(units)} sessions, exceeding "
                f"--max-sessions {args.max_sessions} — rerun with "
                "--confirm (interactive) or --yes (headless)",
                file=sys.stderr)
            return _USAGE

    ok, msg = judge_available()
    if not ok:
        print(f"error: {msg}", file=sys.stderr)
        return _USAGE

    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE

    db_path = args.sessions_db or _default_sessions_db()
    notes = []
    grader = None
    session_grader = None
    if db_path is not None:
        session_grader = _make_g3_grader(cases, db_path, args.packs_dir)
    elif not args.workspace_check:
        notes.append("no sessions.db — success falls back to "
                     "session-completed (no rubric grading)")
    if args.workspace_check:
        ws_grader = abrun.make_workspace_check_grader()
        if session_grader is not None:
            def grader(unit, res, _w=ws_grader, _s=session_grader):
                w, s = _w(unit, res), _s(unit, res)
                merged = dict(s)
                merged["success"] = bool(w.get("success")) and \
                    bool(s.get("success"))
                merged["error"] = bool(w.get("error")) or \
                    bool(s.get("error"))
                return merged
        else:
            grader = ws_grader
            notes.append("workspace-check grading: task's own "
                         "deterministic check decides success")
    else:
        grader = session_grader

    calibration = None
    if args.calibration:
        try:
            prior = _json.loads(Path(args.calibration).read_text(
                encoding="utf-8"))
            calibration = prior.get("calibration")
            if isinstance(calibration, dict):
                calibration = dict(calibration)
                calibration["source"] = args.calibration
        except (OSError, _json.JSONDecodeError) as exc:
            print(f"error: cannot load --calibration: {exc}",
                  file=sys.stderr)
            return _USAGE

    work_dir = (Path(args.work_dir) if args.work_dir
                else Path(tempfile.gettempdir())
                / f"g3-{int(time.time())}")
    try:
        candidate = abrun.candidate_block(
            aa=args.aa,
            prefix=args.variant_a if args.aa else args.variant_b,
            candidate_file=args.candidate_file)
    except abrun.G3Error as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE

    runner = abrun.make_bridge_runner(msg)
    report = abrun.run_ab_v2(
        tasks,
        attempts=args.attempts, seed=args.seed,
        variant_a=args.variant_a,
        variant_b=args.variant_a if args.aa else args.variant_b,
        aa=args.aa,
        work_dir=work_dir,
        runner=runner, grader=grader,
        max_sessions=args.max_sessions,
        max_total_time=args.max_total_time,
        session_timeout=args.session_timeout,
        calibration=calibration,
        candidate=candidate,
        environment=abrun.environment_block(),
    )
    if notes:
        report["notes"] += " | " + " | ".join(notes)
    out = Path(args.out)
    out.write_text(_json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    res = report["results"]
    print(f"verdict: {report['verdict']}  "
          f"(arm A {res['arm_a']['success_rate']}, "
          f"arm B {res['arm_b']['success_rate']}, "
          f"Δ trigger {res['delta_trigger']['mean']})")
    print(f"sessions: {len(report['results']['attempts'])}/"
          f"{len(units)} planned — report written to {out.resolve()}")
    if "aborted early" in report["notes"]:
        print(f"note: {report['notes'].split('|')[-1].strip()}")
    return _FAILED if report["verdict"] == "regresses" else _OK


def _cmd_judge(args: argparse.Namespace) -> int:
    """EV-1: opt-in LLM judge — fail-closed without DEVIN_BRIDGE_CMD."""
    import json as _json
    from devin_evals.judge import build_prompt, judge_available, run_judge
    ok, msg = judge_available()
    if not ok:
        print(f"error: {msg}", file=sys.stderr)
        return _USAGE
    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    case = next((c for c in cases if c.id == args.case), None)
    if case is None:
        print(f"error: no case {args.case!r} in {args.evals}", file=sys.stderr)
        return _USAGE
    evidence = {"note": "offline evidence assembly — the judge sees "
                        "the case description + your question"}
    res = run_judge(
        build_prompt(case.id, case.description, evidence, args.question),
        cwd=args.cwd, case_id=case.id)
    print(_json.dumps({"case": case.id, "deterministic": False, **res},
                      indent=2))
    if not res.get("ok"):
        return _FAILED
    return _OK if res["verdict"] == "pass" else _FAILED

_OK, _FAILED, _USAGE = 0, 1, 2


def _cmd_packs(args: argparse.Namespace) -> int:
    packs = builtin_packs()
    if not packs:
        print("no built-in packs")
        return _OK
    import json as _json
    for pack_id, p in sorted(packs.items()):
        data = _json.loads(p.read_text(encoding="utf-8"))
        checks = len(data.get("rubric", []))
        print(f"{pack_id}\t{checks} checks\t{data.get('description', '')}")
    return _OK


def _cmd_list(args: argparse.Namespace) -> int:
    try:
        cases = load_cases(args.evals, packs_dir=args.packs_dir)
    except CaseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    if not cases:
        print(f"no eval cases in {args.evals}")
        return _OK
    for c in cases:
        ref = c.session_ref or "(no session_ref)"
        checks = f"{len(c.checks)} check{'s' if len(c.checks) != 1 else ''}"
        print(f"{c.id}\t{checks}\t{ref}\t{c.description}")
    return _OK


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        report = run_evals(args.evals, args.sessions_db, out_dir=args.out,
                           packs_dir=args.packs_dir)
    except (CaseError, SchemaError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    for c in report["cases"]:
        n = len(c["checks"])
        n_ok = sum(1 for ch in c["checks"] if ch["passed"])
        suffix = f"{n_ok}/{n} checks" if n else c["detail"]
        print(f"{c['status'].upper():5}  {c['id']}  ({suffix})")
    s = report["summary"]
    print(
        f"score: {s['passed']}/{s['total']} cases passed"
        f" — report written to {Path(args.out).resolve()}"
    )
    return _FAILED if (s["failed"] or s["errored"]) else _OK


def _cmd_corpus_generate(args: argparse.Namespace) -> int:
    try:
        manifest = generate_corpus(
            args.out, seed=args.seed)
    except (CaseError, SchemaError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    n = len(manifest["cases"])
    gaps = len(manifest["known_gaps"])
    print(f"corpus: {n} golden case(s) ({gaps} documented grader gap(s)) "
          f"via generator={manifest['generator']} -> {Path(args.out).resolve()}")
    print(f"verify with: devin-evals corpus verify --corpus {args.out}")
    return _OK


def _cmd_corpus_verify(args: argparse.Namespace) -> int:
    try:
        report = verify_corpus(
            args.corpus, strict=args.strict, out_dir=args.out)
    except (CaseError, SchemaError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _USAGE
    for c in report["cases"]:
        tag = {"match": "MATCH", "gap": "GAP", "mismatch": "MISMATCH"}[
            c["result"]]
        print(f"{tag:8}  {c['id']}  "
              f"(expected={c['expected_status']} actual={c['actual_status']})")
    gaps = [c for c in report["cases"] if c["result"] == "gap"]
    if gaps:
        print("known grader gaps (documented, tolerated):")
        for c in gaps:
            print(f"  {c['defect'] or c['id']}: {c['known_gap']}")
    s = report["summary"]
    print(f"corpus: {s['matched']}/{s['total']} expectations matched, "
          f"{s['gaps']} known gap(s), {s['mismatched']} mismatch(es)")
    return _OK if s["ok"] else _FAILED


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="devin-evals",
        description="Grade agent sessions against deterministic rubrics "
        "(offline replay over Devin's sessions.db). Read-only.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="replay eval cases against a sessions.db")
    run.add_argument("--evals", required=True, help="directory of *.json eval cases")
    run.add_argument("--sessions-db", required=True, help="path to sessions.db")
    run.add_argument(
        "--packs-dir",
        help="directory of custom rubric packs (shadows the built-ins)",
    )
    run.add_argument(
        "--out",
        default="eval-report",
        help="output directory for report.json/report.md (default: eval-report)",
    )
    run.set_defaults(func=_cmd_run)

    ls = sub.add_parser("list", help="list eval cases in a directory")
    ls.add_argument("--evals", required=True, help="directory of *.json eval cases")
    ls.add_argument("--packs-dir", help="directory of custom rubric packs")
    ls.set_defaults(func=_cmd_list)

    pk = sub.add_parser(
        "packs", help="list the built-in rubric packs (bugfix, feature, refactor)")
    pk.set_defaults(func=_cmd_packs)

    ju = sub.add_parser(
        "judge",
        help="EV-1: opt-in LLM judge for one case (non-deterministic; "
        "needs DEVIN_BRIDGE_CMD — sessions are labelled judge:<id> for "
        "janitor to reap)")
    ju.add_argument("case", help="eval case id to judge")
    ju.add_argument("--evals", required=True,
                    help="directory of *.json eval cases")
    ju.add_argument("--packs-dir", help="directory of custom rubric packs")
    ju.add_argument("--cwd", default=".",
                    help="working dir for the judge session (default: .)")
    ju.add_argument("--question", required=True,
                    help="the judgment question, e.g. 'did the session "
                    "address the user request?'")
    ju.set_defaults(func=_cmd_judge)

    ab = sub.add_parser(
        "ab-run",
        help="EV-5/G3: preregistered A/B suite (tasks manifest, k attempts "
        "per arm, seeded ABBA/BAAB interleave, budget caps, g3-report). "
        "With --task it falls back to the deprecated two-session quick "
        "mode. Consumes real tokens; fail-closed without DEVIN_BRIDGE_CMD")
    ab.add_argument("--task",
                    help="simple mode (deprecated): one task prompt sent "
                    "to exactly two sessions")
    ab.add_argument("--variant-a", default="",
                    help="prefix for arm A (e.g. no-skill baseline)")
    ab.add_argument("--variant-b", default="",
                    help="prefix for arm B (e.g. skill context)")
    ab.add_argument("--repo",
                    help="working dir for both sessions (simple mode only)")
    ab.add_argument("--tag", default="ab",
                    help="label tag, simple mode (default: ab)")
    ab.add_argument("--evals", help="eval cases dir for post-run grading")
    ab.add_argument("--case", help="case id to grade (default: first case)")
    ab.add_argument("--packs-dir", help="directory of custom rubric packs")
    ab.add_argument("--sessions-db",
                    help="sessions.db for grading (default: autodetect)")
    ab.add_argument("--timeout", type=int, default=45 * 60,
                    help="per-session timeout in seconds (simple mode)")
    ab.add_argument("--tasks",
                    help="G3 tasks manifest dir (default: <evals>/tasks, "
                    "falling back to ./tasks)")
    ab.add_argument("--attempts", type=int, default=5,
                    help="attempts per arm per task (default: 5, min: 3)")
    ab.add_argument("--seed", type=int, default=73001,
                    help="seed for the task order + bootstrap (default: 73001)")
    ab.add_argument("--max-sessions", type=int,
                    help="hard cap on total sessions; aborts the run when hit")
    ab.add_argument("--max-total-time", type=float,
                    help="hard cap on total wall-clock seconds")
    ab.add_argument("--session-timeout", type=int, default=45 * 60,
                    help="per-attempt timeout in seconds (default: 2700)")
    ab.add_argument("--work-dir",
                    help="run dir for per-attempt workspace copies "
                    "(default: <tmp>/g3-<ts>)")
    ab.add_argument("--confirm", action="store_true",
                    help="interactively confirm when the plan exceeds "
                    "--max-sessions")
    ab.add_argument("--yes", action="store_true",
                    help="headless confirmation of an over-cap plan")
    ab.add_argument("--aa", action="store_true",
                    help="A/A calibration mode: arm B uses the arm A prefix")
    ab.add_argument("--workspace-check", action="store_true",
                    help="grade each attempt by the task's own check in "
                    "the workspace copy (pytest tests + check_structure.py "
                    "when present) — the mode for hermetic packs; combined "
                    "with session rubric grading when sessions.db resolves")
    ab.add_argument("--calibration",
                    help="prior g3-report.json whose calibration block "
                    "gates the 'improves' verdict")
    ab.add_argument("--candidate-file",
                    help="file whose sha256 is pinned as candidate.sha256")
    ab.add_argument("--out", default="g3-report.json",
                    help="report path (default: g3-report.json)")
    ab.add_argument("--dry-run", action="store_true",
                    help="print the session plan for free; creates nothing")
    ab.set_defaults(func=_cmd_abrun)

    co = sub.add_parser(
        "corpus",
        help="golden-case corpus of labeled synthetic defect sessions (EV-3)")
    csub = co.add_subparsers(dest="corpus_command", required=True)

    cg = csub.add_parser(
        "generate",
        help="materialize synthetic sessions.db files + eval cases into a dir")
    cg.add_argument(
        "--out", default="corpus",
        help="output directory for the corpus (default: corpus/)")
    cg.add_argument(
        "--seed", type=int, default=0xDEE4,
        help="deterministic seed (default: 0xDEE4)")
    cg.set_defaults(func=_cmd_corpus_generate)

    cv = csub.add_parser(
        "verify",
        help="replay a generated corpus and report expected-vs-actual")
    cv.add_argument(
        "--corpus", default="corpus",
        help="corpus directory produced by `corpus generate` (default: corpus/)")
    cv.add_argument(
        "--strict", action="store_true",
        help="fail the gate on documented grader gaps too, not just mismatches")
    cv.add_argument(
        "--out",
        help="optional output directory for verify-report.json")
    cv.set_defaults(func=_cmd_corpus_verify)

    dre = sub.add_parser(
        "dream",
        help="generate synthetic sessions with known verdicts (absorbed devin-dream)")
    from devin_evals.dream.cli import add_dream_subcommands
    add_dream_subcommands(dre.add_subparsers(dest="dream_command", required=True))
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
