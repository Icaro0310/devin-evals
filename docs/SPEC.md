# SPEC — `devin-evals`

## 1. Problem

"Is the agent getting better?" has no number. Prompts get tweaked, models get
swapped, rules files grow — and quality is judged by vibes, one anecdotal run
at a time. SWE-bench-style harnesses exist, but they grade public repos, not
*your* agent's actual sessions on *your* machine.

Evidence: every Devin session already records its full transcript and —
crucially — `tool_call_state` ground truth (which tools ran, with what args,
what exit codes) in `sessions.db`. That data is sitting unused.

## 2. Devin extra (and the 3 tests)

**Extra:** rubric checks run against `tool_call_state` ground truth via
`devin-internals` — "must call devin-redact before publishing" becomes a
checkable fact, not an LLM judgement call.

- **Side-by-side competitor:** generic eval frameworks (OpenAI evals,
  promptfoo, Braintrust) can assert on output text. They cannot assert "the
  agent invoked tool X with args containing Y" because they never see Devin's
  recorded tool-call table. Ours can.
- **No-Devin:** without a `sessions.db` there is no `tool_call_state`; the
  tool/exit-code graders and the whole offline-replay mode disappear.
- **One sentence:** *"It replays your recorded agent sessions against a
  rubric, so quality has a number you can watch over time."*

## 3. Scope (M1 — done)

- `evals/<name>.json` case format: `id`, `description`, `session_ref` (or
  `prompt_context` for future live mode), `rubric` (list of checks).
- Six deterministic graders, stdlib-only at runtime:
  `contains`, `not_contains`, `tool_called(name, args_substr, min_calls)`,
  `file_exists(path)`, `exit_code(value, mode)`, `no_secrets`.
- Offline replay runner over `sessions.db` → `report.json` + `report.md`,
  per-case PASS/FAIL/SKIP/ERROR + aggregate score. Deterministic: reruns are
  byte-identical.
- CLI: `devin-evals run --evals <dir> --sessions-db <db> [--out dir]`,
  `devin-evals list --evals <dir>`.
- `devin_evals.demo` — synthetic sessions.db builder; `evals/` ships 3
  sample cases (pass / fail / tool-call).

## 4. Non-scope (M1)

- No live agent execution (offline replay only) — live mode is M2+.
- No LLM-as-judge / semantic graders — deterministic only, M2 opt-in.
- No trend tracking / baseline diffing ("score went down") — M2.
- No writes, ever: the store is opened read-only; the tool never mutates
  `sessions.db`, the workspace, or anything outside `--out`.

## 5. Interfaces

| Interface | Description |
|---|---|
| `devin_evals.cases` | `load_cases(dir) -> [EvalCase]` — JSON parsing + eager validation (`CaseError`) |
| `devin_evals.graders` | `Evidence`, `grade_check(name, params, ev) -> CheckResult`, `GRADERS` registry |
| `devin_evals.runner` | `run_evals(evals_dir, sessions_db, out_dir) -> report dict` |
| `devin_evals.demo` | `build_demo_sessions_db(path)` — CLI-verifiable fixture |
| CLI `devin-evals` | `run` (0=all pass, 1=any fail/error, 2=usage or IO error) · `list` |

## 6. Output format / data contract

### Eval case (`evals/<name>.json`)

```json
{
  "id": "demo-pass",                      // optional — defaults to file stem
  "description": "what this case checks",
  "session_ref": "Demo eval session",     // session id OR title; else "prompt_context"
  "rubric": [
    {"grader": "contains", "text": "all tests pass"},
    {"grader": "tool_called", "name": "run_shell", "args_substr": "pytest"}
  ]
}
```

### Grader contract

| grader | required params | optional params | corpus |
|---|---|---|---|
| `contains` | `text` | — | transcript (messages + prompt history); case-sensitive |
| `not_contains` | `text` | — | transcript |
| `tool_called` | `name` | `args_substr`, `min_calls` (1) | parsed `tool_call_json` names + raw args |
| `file_exists` | `path` | — | filesystem; relative = under session `working_directory` |
| `exit_code` | — | `value` (0), `mode` (`all`/`any`/`last`) | `"exit_code"` fields in `tool_call_update_json` |
| `no_secrets` | — | — | transcript **and** tool-call JSON (secrets leak via args) |
| `no_pii` | — | — | transcript + tool-call JSON (email, CPF) |
| `tool_output` | `text` | `present` (false) | `tool_call_update_json` substring |
| `regex` | `text` | `present` (false) | `tool_call_update_json` regex match |
| `no_split_secrets` | — | — | seam-join of adjacent tool payloads |

### Report (`report.json`)

```json
{
  "tool": "devin-evals", "version": "0.1.0",
  "sessions_db": "...", "schema_version": 17, "evals_dir": "...",
  "cases": [{"id", "status": "pass|fail|skip|error", "score": 0.0-1.0,
             "detail", "checks": [{"grader", "params", "passed", "error", "detail"}]}],
  "summary": {"total", "passed", "failed", "skipped", "errored",
              "score": passed/total}
}
```

`report.md` renders the same data as a summary table + per-case check
details. No timestamps — reruns are byte-identical.

Statuses: **skip** = no `session_ref` (live-only case); **error** = case
couldn't be evaluated (unknown session_ref, bad params) — harness/config
bug, distinguished from a real **fail**.

## 7. Fixtures and tests (TDD — fixtures first)

- `devin_internals.fixtures` supplies the real v17 DDL + synthetic rows;
  `tests/conftest.py` adds one fully-controlled session (known transcript,
  named tool calls, exit codes, real tmp working dir).
- Tests: every grader (incl. corpus boundaries — `contains` must NOT match
  tool args), JSON parsing/validation errors, runner report shape, statuses,
  deterministic rerun, CLI exit codes, shipped `evals/` vs demo db.
- **62 tests, all green** (Windows, Python 3.11.9, pytest 9.1.1).

## 8. Risks and mitigation

| Risk | Mitigation |
|---|---|
| `tool_call_json` field names vary across Devin versions | name extraction tries `name`/`tool_name`/`tool`/`kind`; misses produce "tool never called (seen: ...)" details, not crashes |
| sessions.db schema bump | gated by `devin-internals`' detector — refuses loudly on unknown versions instead of misreading |
| `session_ref` by title collides | id checked first; titles are user-visible ambiguity — documented |
| Eval quality = "grading the grader" | deterministic graders only; every check reports its evidence in `detail` |

## 9. Decisions

- **JSON, not YAML** for eval files (KICKOFF-M1 env notes): keeps runtime
  stdlib-only — the only dependency is `devin-internals-spec` itself.
- **Secret regexes vendored** from `devin-redact` (same author, MIT) — no
  runtime dep on it; only SECRET categories, PII patterns intentionally
  excluded.
- `score = passed/total` — skips and errors count as not-passed; simplest
  honest number for "how green is the suite".

## 10. Definition of done (M1) — all met

- [x] `evals/*.json` loads, validates, sorts deterministically
- [x] All six graders implemented + unit-tested
- [x] Runner produces report.json + report.md; reruns byte-identical
- [x] CLI `run`/`list` verified on fixture; correct exit codes
- [x] 3 sample evals (pass / fail / tool-call) grade the demo db correctly
- [x] `python -m pytest` — 62 green

## 11. M2 queue

1. LLM-as-judge graders (opt-in, marked non-deterministic in report).
2. Baseline diff mode (`--baseline report-old.json` → "score went down").
3. Regression CI mode (exit code wired to diff, not just pass/fail).
4. Live mode: `prompt_context` cases spawn a session instead of replaying.
5. PyPI publish.
