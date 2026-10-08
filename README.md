<div align="center">

<img src="assets/banner.svg" alt="devin-evals" width="100%"/>

<a href="https://github.com/Icaro0310/devin-evals/actions/workflows/tests.yml"><img src="https://github.com/Icaro0310/devin-evals/actions/workflows/tests.yml/badge.svg" alt="tests"/></a>


<a href="https://github.com/Icaro0310/devin-evals/actions/workflows/ci.yml"><img src="https://github.com/Icaro0310/devin-evals/actions/workflows/ci.yml/badge.svg" alt="ci"/></a>
<a href="https://scorecard.dev/viewer/?uri=github.com/Icaro0310/devin-evals"><img src="https://api.scorecard.dev/projects/github.com/Icaro0310/devin-evals/badge" alt="OpenSSF Scorecard"/></a>
<a href="https://deepwiki.com/Icaro0310/devin-evals"><img src="https://deepwiki.com/badge.svg" alt="DeepWiki"/></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT"/></a>
<a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+"/></a>
<a href="https://github.com/Icaro0310/devin-evals"><img src="https://img.shields.io/github/stars/Icaro0310/devin-evals" alt="GitHub stars"/></a>
<a href="https://github.com/Icaro0310/devin-evals/commits/main"><img src="https://img.shields.io/github/last-commit/Icaro0310/devin-evals" alt="Last commit"/></a>
<a href="https://github.com/Icaro0310/awesome-devin"><img src="https://img.shields.io/badge/part%20of-devin--*-ecosystem-7c3aed" alt="devin-* ecosystem"/></a>
<a href="https://github.com/Icaro0310/devin-evals/issues"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs welcome"/></a>
</div>

<!-- DEVIN-ECO:BEGIN -->
> **Part of the [DEVIN ecosystem](https://github.com/Icaro0310/awesome-devin)**  
> Track: Verify · Nature: product  
> For: QA engineers, AI engineers  
> Interface: CLI / Python library
<!-- DEVIN-ECO:END -->

# devin-evals

> **Unofficial community project.** Not affiliated with, endorsed by, or
> sponsored by Cognition AI. "Devin" is a trademark of Cognition AI.

**[Linux](README.linux.md)** · **[Personal Windows](README.windows.md)** · **[Corporate Windows](README.corporate-windows.md)**

Part of the [awesome-devin](https://github.com/Icaro0310/awesome-devin) ecosystem: the curated hub for the devin-* tools.

An evaluation harness for agent work: define graded cases (session +
rubric), replay them against recorded Devin sessions, and score quality
over time — so "is the agent getting better?" has a number.

## The problem

You tune prompts, rules files and models — and judge the result by vibes,
one anecdotal run at a time. There is no regression signal. Meanwhile every
Devin session already records, in `sessions.db`, its full transcript and
the `tool_call_state` table: *which tools ran, with what arguments, with
what exit codes*. That ground truth is sitting unused on your disk.

## Prior art

Generic eval frameworks ([OpenAI evals](https://github.com/openai/evals),
[promptfoo](https://github.com/promptfoo/promptfoo),
[Braintrust](https://www.braintrust.dev/)) grade *output text* — usually
through an LLM judge. SWE-bench-style harnesses grade public repos, not
your agent's real sessions. devin-evals adapts the rubric/graders idea; it
does not reinvent it. What it adds is the corpus: deterministic checks
over Devin's recorded tool-call ground truth.

## What makes it Devin-native

Rubrics like **"must call `devin_redact` before publishing"** become
checkable facts. Graders read `tool_call_state` via
[`devin-internals-spec`](https://github.com/Icaro0310/devin-internals-spec),
so a check asserts "tool `run_shell` was invoked with `pytest` in its args
and exited 0" — a fact, not an LLM judgement call.

- *Side-by-side:* promptfoo cannot assert "tool X was called with args
  containing Y" — it never sees Devin's tool-call table.
- *No-Devin:* no `sessions.db`, no tool-call graders, no replay mode.

## Install

Requires Python ≥ 3.10 and `pipx` or `uv`. Per-OS setup lives in the platform guides: [Linux](README.linux.md) · [Personal Windows](README.windows.md) · [Corporate Windows](README.corporate-windows.md).

<!-- DIST-STATUS:BEGIN — generated from devin-powerups/registry.json -->
> **Source-only distribution.** This tool is not yet published to PyPI.
> Install from source:
>
> ```bash
> pipx install git+https://github.com/Icaro0310/devin-evals.git
> # or
> uv tool install git+https://github.com/Icaro0310/devin-evals.git
> ```
<!-- DIST-STATUS:END -->

From a checkout: `pip install -e .`

Runtime deps: `devin-internals-spec` only — no network, no LLM calls.

## Usage

Write cases in `evals/*.json`:

```json
{
  "id": "redact-before-publish",
  "description": "Report workflow must stay hygienic",
  "session_ref": "Refinery session 2026-09-29",
  "rubric": [
    { "grader": "tool_called", "name": "devin_redact" },
    { "grader": "tool_called", "name": "run_shell", "args_substr": "pytest" },
    { "grader": "exit_code", "value": 0, "mode": "all" },
    { "grader": "contains", "text": "all tests pass" },
    { "grader": "no_secrets" }
  ]
}
```

`session_ref` matches a session **id or title** in `sessions.db`. Then:

```powershell
# Windows PowerShell
devin-evals list --evals evals
devin-evals run --evals evals --sessions-db "$env:APPDATA\devin\cli\sessions.db" --out report
```

```bash
# Linux
sessions_db="${XDG_DATA_HOME:-$HOME/.local/share}/devin/cli/sessions.db"
devin-evals list --evals evals
devin-evals run --evals evals --sessions-db "$sessions_db" --out report
```

`run` writes `report/report.json` + `report/report.md` (per-case
PASS/FAIL/SKIP/ERROR + aggregate score; reruns are byte-identical) and
exits 0 if everything passed, 1 on failures, 2 on usage/IO errors.

**Try it without a real Devin install:**

```bash
python -m devin_evals.demo demo.db
devin-evals run --evals evals --sessions-db demo.db
```

The shipped `evals/` directory contains a passing case, an intentionally
failing case, and a tool-call-ground-truth case.

### Graders

| grader | what it checks |
|---|---|
| `contains` / `not_contains` | literal substring in the transcript (case-sensitive) |
| `tool_called` | tool `name` called ≥`min_calls`, optional `args_substr` on call JSON |
| `file_exists` | path on disk — relative resolves under the session's `working_directory` |
| `exit_code` | recorded exit codes match `value` per `mode` (`all`/`any`/`last`) |
| `no_secrets` | zero secret-shaped strings (vendored devin-redact patterns) in transcript + tool JSON |
| `no_pii` | zero PII-shaped strings (email, CPF) in transcript + tool JSON |
| `tool_output` | `text` presence in tool-call output JSON (`present` inverts) |
| `no_split_secrets` | no secret-shaped match spanning the seam of two tool payloads |

## Works with Devin alone (Devin-only mode)

devin-evals scores recorded sessions with deterministic rubrics — no LLM
calls, no network access, nothing beyond Devin's own session data and Python.

## Platform support

Pure stdlib Python — identical behavior on Windows, Linux and macOS. CI runs
the suite on `windows-latest` + `ubuntu-latest`; the target file or
directory is always an explicit argument, so there are no
platform-specific paths.


### Rubric packs (EV-4)

Reusable check sets for common session types, so replay works out of the
box. Built-ins shipped with the package: `bugfix`, `feature`, `refactor` —
each is a small hygiene rubric (clean exit codes, no tracebacks, no
secrets) meant to be **extended** by the case's own `rubric`.

```json
{
  "session_ref": "my-session-id",
  "packs": ["bugfix"],
  "rubric": [ { "grader": "contains", "text": "test_regression" } ]
}
```

Pack checks run **before** the case's own checks. List them with
`devin-evals packs`; override or add your own with `--packs-dir <dir>`
(a `<name>.json` file there shadows the built-in of the same name).

### Synthetic sessions (`dream`)

The `dream` subgroup generates synthetic Devin sessions with known
verdicts — regression and adversarial fixtures. This was the standalone
`devin-dream` repository, absorbed into this package:

```bash
devin-evals dream unit   --out out/                 # one dir per defect + expected.json
devin-evals dream unit   --out out/ --defect D01 D03
devin-evals dream inject --out adv/ --n 5           # adversarial (D07/D08) + scorecard
devin-evals dream fleet  --out big/ --sessions 2000 --seed 1 --manifest
```

Secrets and PII in generated fixtures are always obviously fake
public-documentation values — nothing real is ever generated or read.

### Golden corpus (EV-3)

`devin-evals corpus` materializes and replays a deterministic corpus of
**labeled synthetic sessions** — nine defect classes (D01–D09, generated
by `devin_evals.dream`, the absorbed devin-dream catalogue) with a known
verdict each — plus the matching `evals/*.json` cases whose rubrics
encode those verdicts in gradable form. It is the CI gate that proves
evals, fixtures and graders agree:

```bash
devin-evals corpus generate --out .corpus   # sessions.db + evals/ + corpus.json
devin-evals corpus verify  --corpus .corpus # expected-vs-actual per case
```

Everything is deterministic given `--seed` and **synthetic only**: the
corpus must never point at a real `sessions.db`.

The corpus is also **versioned** in the repo: `corpus/evals/*.json` and
`corpus/corpus.json` are committed, while the `sessions*.db` files are
always regenerated in place (never committed — `*.db` is gitignored).
`tools/regen-corpus.py` rebuilds them deterministically and gates CI on
drift:

```bash
python tools/regen-corpus.py          # rebuild corpus/ in place
python tools/regen-corpus.py --check  # exit 1 if committed corpus diverges
python tools/regen-corpus.py --verify # rebuild + replay expectations
```

`--check` reuses the `seed` recorded in `corpus/corpus.json`; the
committed corpus is generated by the bundled `devin_evals.dream`
package, so the gate is fully self-contained.

Each case carries `expected_status` (the verdict the case *should* reach:
`pass` for the clean control D03, `fail` where a defect must be caught,
`error` for the D06 schema-drift canary whose v18 db is refused at open
time). `verify` prints `MATCH` / `GAP` / `MISMATCH` per case and exits 1
on any undocumented mismatch; `--strict` also fails on documented gaps.

The D05/D07/D09 grader gaps are closed: `no_pii` covers PII, `tool_output`
scans tool-call output for injected instructions, and `no_split_secrets`
catches credentials split across payloads. Remaining known limits:
verdict granularity is coarser than the source catalogue (qa-pack's
UNVERIFIED/PARTIAL both collapse to `fail`), and D08's "quarantined" is
graded by a transcript-level proxy (`not_contains` on the unsafe policy).


`session_ref` accepts selectors (EV-2), each resolving to the *most
recent* match: `latest`, `project:<substr>` (matches
`working_directory`), `window:<YYYY-MM-DD>:<YYYY-MM-DD>` — plus exact id
or title as before.


`devin-evals judge <case> --question "…"` (EV-1, **opt-in**) asks a live
LLM to grade one case. Non-deterministic, off by default, fail-closed:
needs `DEVIN_BRIDGE_CMD` (drives `devin-bridge`, which gates via policy)
and uses the free model unless `DEVIN_JUDGE_MODEL` overrides. Sessions
are labelled `judge:<case>` so `devin-janitor` can reap the noise.


`ab-run` (EV-5/G3, **opt-in**) is the A/B gate: a suite of tasks run in
two arms (variant prefixes A vs B), k attempts per arm, graded by the
same deterministic rubrics — the with-skill/without-skill proof loop.
Consumes real tokens, fail-closed without `DEVIN_BRIDGE_CMD`; sessions
are labelled `g3-ab:<task>:<variant>:<attempt>` for janitor.

```bash
devin-evals ab-run --evals evals --tasks evals/tasks \
  --attempts 5 --seed 73001 --max-sessions 80 --yes \
  --sessions-db "$sessions_db" --out g3-report.json
```

The suite lives in a **tasks manifest dir** (default `<evals>/tasks`,
falling back to `./tasks`, or `--tasks DIR`). Two layouts are accepted:
an index `manifest.json` (a list of `{"id", "kind", "type", "prompt",
"dir"|"workspace"}` entries), or one `*.json` per task
(`{"id", "kind": "bugfix|feature|refactor", "type": "trigger|control",
"prompt", "workspace"}`). The repo ships a hermetic 8-task pack under
`tasks/` (5 trigger + 3 control — see `tasks/README.md`). A run requires
**≥5 trigger + ≥3 control** tasks; trigger tasks measure the effect,
control tasks detect collateral regressions. Each attempt runs in its
own `shutil.copytree` of the task workspace under `--work-dir` (default
`<tmp>/g3-<ts>`) — the originals are never touched and `_solution/`
reference fixes are never copied into an attempt. Order is
deterministic: tasks shuffled by `--seed`, arms interleaved ABBA/BAAB
across tasks to spread temporal drift.

Success = all checks pass. The default grader replays the session
through the rubrics (`--sessions-db`, autodetected): the eval case
matching the task id when present, else the pack matching the task
`kind`. For hermetic workspace packs, `--workspace-check` instead runs
the task's own deterministic check inside the attempt copy (`pytest
tests -q`, plus `check_structure.py` when present) — combined with the
session rubric when a sessions.db resolves.

Budget caps (`--max-sessions`, `--max-total-time`, `--session-timeout`)
abort the whole run when hit and report what ran; over-cap plans need
`--confirm` (interactive) or `--yes` (headless). `--dry-run` prints the
full session plan for free. Timed-out/failed attempts count as
failures, are recorded separately, and are never retried.

**Preregistration**: tasks, k, seed, caps and every verdict threshold
are frozen into the report's `design` block *before* the first session
runs (`preregistered: true`). The report (`g3-report/0.1`, `--out`)
contains only aggregates and session ids — never session content.

**Verdicts** (Wilson CIs on arm rates; bootstrap CI over the mean
per-task Δ = rate<sub>B</sub> − rate<sub>A</sub> on trigger tasks, 10k
fixed-seed resamples):

| verdict | condition |
|---|---|
| `regresses` | Δ CI entirely < 0, OR any control task Δ ≤ −0.4, OR safety worsens (`denials_b > denials_a` when `denials_a == 0`) |
| `improves` | Δ CI over trigger tasks entirely > 0 AND no regress condition AND calibration ok |
| `no-detectable-effect` | Δ CI contains 0 AND CI width ≤ 0.30 |
| `inconclusive` | everything else: CI wider than 0.30, baseline ceiling (≥95%) or floor (≤5%), >20% aborts in an arm, calibration absent/failed where needed |

**Calibration**: `improves` additionally requires a passing A/A
calibration — run `ab-run --aa` (arm B gets the arm A prefix) to measure
the harness's own false-positive rate, then point `--calibration
prior-report.json` at it. Without it, an otherwise-improving run is
honestly reported `inconclusive`.

**Honest power note**: with the minimum suite (8 tasks) at k=5 the Δ CI
is roughly ±0.22 — only effects ≥ ~0.25 are detectable. This gate
catches large regressions, not subtle improvements; add tasks, not
attempts, to shrink the CI.

Passing `--task` still runs the deprecated simple mode (exactly two
sessions, `ab-run:<tag>:<variant>` labels) — kept for quick smoke
checks, not a gate.

## Limitations

- **Offline replay only** (M1): grades recorded sessions, cannot spawn new
  ones. `prompt_context`-only cases report SKIP.
- **Deterministic only** (M1): no LLM-as-judge; `contains` is a literal,
  case-sensitive substring — it cannot tell "all tests pass" from "not all
  tests pass" semantically.
- Depends on Devin's *private, versioned* internals — a `sessions.db`
  schema bump makes `devin-internals` refuse loudly rather than misread.
- `tool_called` infers the tool name from `name`/`tool_name`/`tool`/`kind`
  keys in `tool_call_json`; unknown shapes degrade to "tool never called"
  details, not crashes.
- `file_exists` checks the filesystem *now* — replaying an old session
  whose workspace was cleaned will fail that check.

## Development

```bash
pip install -e ".[dev]"
python -m pytest     # 177 tests
```

## When to use this

- You changed a prompt, rules file or model and want a numeric regression signal across recorded sessions.
- You want to assert tool-call behavior — e.g. "must call `devin_redact` before publishing" — as a checkable fact.
- You need reproducible scoring: deterministic graders make reruns byte-identical, no LLM judge involved.
- You want to try it without a Devin install: `python -m devin_evals.demo demo.db` builds a sample DB.

## When NOT to use this

- You need semantic judgement of free text — `contains` is a literal, case-sensitive substring and there is no LLM-as-judge (M1).
- You need to spawn new sessions — this is offline replay of recorded sessions only.
- Your agent is not Devin — the ground truth comes from `sessions.db`/`tool_call_state`.

## FAQ

**How do I regression-test changes to my Devin prompts or rules?** Define cases in `evals/*.json` pairing a `session_ref` (session id or title) with a rubric, then run `devin-evals run --evals evals --sessions-db <path> --out report`. Each rubric item is a deterministic grader over the recorded tool calls, so reruns produce identical scores — a real before/after comparison.

**Does devin-evals use an LLM judge?** No. All graders (`contains`, `tool_called`, `file_exists`, `exit_code`, `no_secrets`) are deterministic checks over `tool_call_state` and the transcript. That makes scores reproducible but also literal — it cannot evaluate semantic quality of prose.

**Can I use devin-evals without Devin installed?** Yes, for a demo: `python -m devin_evals.demo demo.db` creates a synthetic sessions.db and the shipped `evals/` directory contains passing and intentionally failing cases. For real use you need a `sessions.db` from actual Devin sessions.

## License

MIT — see [LICENSE](LICENSE).


---

If this saved you debugging time, a ⭐ on the repo helps others find it.
