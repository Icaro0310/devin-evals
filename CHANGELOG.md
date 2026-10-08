# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Graders `no_pii`, `tool_output` and `no_split_secrets`, closing the
  documented corpus gaps: D05 (PII in prompt — email + CPF patterns),
  D07 (injected instruction inside tool-call output JSON) and D09
  (credential split across two tool payloads, detected by seam-spanning
  joins). Corpus verification now reports 9/9 expectations matched.
- `dream` subcommand group (`devin-evals dream unit|inject|fleet`) —
  absorbs the standalone `devin-dream` repository: deterministic synthetic
  Devin sessions with known verdicts (defects D01–D09).
- `ab-run` G3 harness (EV-5 v2) — preregistered A/B suite over a tasks
  manifest dir (≥5 trigger + ≥3 control, `<evals>/tasks` or `--tasks`),
  k attempts per arm (`--attempts`, min 3), seeded ABBA/BAAB
  interleaving (`--seed`), budget caps (`--max-sessions`,
  `--max-total-time`, `--session-timeout`) that abort the run and report
  partials, per-attempt `shutil.copytree` workspace isolation under
  `--work-dir`, and a `g3-report/0.1` report (`--out`) with aggregates +
  session ids only. Verdicts: `regresses` / `improves` /
  `no-detectable-effect` / `inconclusive` via Wilson CIs and a
  fixed-seed 10k-draw bootstrap of per-task deltas (`g3stats.py`).
  `--aa` runs an A/A calibration of the harness's false-positive rate;
  `--calibration <report>` gates the `improves` verdict. Session labels
  are `g3-ab:<task>:<variant>:<attempt>` for janitor. Task suites accept
  an index `manifest.json` (the shipped `tasks/` pack) or per-task
  `*.json` files; `--workspace-check` grades attempts by the task's own
  deterministic check in the workspace copy (`_solution/` is never
  copied into an attempt).
- `devin-evals corpus generate|verify` (EV-3) — deterministic golden corpus
  of labeled synthetic defect sessions (D01–D09, devin-dream catalogue)
  plus matching eval cases with `expected_status`/`known_gap` metadata;
  `verify` is the CI gate comparing expected-vs-actual. Session specs come
  from `devin_dream.defects` when importable, else the vendored copy in
  `devin_evals._vendored_dream` (identical corpora either way).
- Committed golden corpus at `corpus/` (`evals/*.json` + `corpus.json`;
  the generated `sessions*.db` stay gitignored) with
  `tools/regen-corpus.py` rebuilding it deterministically — `--check`
  fails CI when the committed corpus diverges from regenerated output.
- `runner._message_text` now also reads the `content` key in
  `chat_message` blobs (the ACP/dream shape), not only `text`.
- `devin_evals.cases` — `evals/<name>.json` case format (JSON chosen over
  YAML to stay stdlib-only) with eager validation (`CaseError`).
- `devin_evals.graders` — six deterministic graders over an `Evidence`
  value object: `contains`, `not_contains`, `tool_called`, `file_exists`,
  `exit_code`, `no_secrets` (secret regexes vendored from devin-redact).
- `devin_evals.runner` — offline replay against `sessions.db` via
  `devin-internals-spec`; deterministic `report.json` + `report.md` with
  per-case PASS/FAIL/SKIP/ERROR and aggregate score.
- `devin-evals` CLI: `run` (exit 0/1/2) and `list` subcommands.
- `devin_evals.demo` — synthetic sessions.db builder
  (`python -m devin_evals.demo <db>`) for trying the CLI without Devin.
- `evals/` — three sample cases (pass, intentionally-fail, tool-call).
- `docs/SPEC.md`, `STATUS.md`, real bilingual READMEs; 62 tests.
- Initial scaffold from `devin-repo-template`.

### Removed

- `devin_evals._vendored_dream` fallback and the `corpus generate
  --generator` flag — `devin_evals.dream` is the single generator now.

### Changed

- `labeler.yml` is now a thin caller of the shared reusable workflow in `devin-powerups` (`@v1`); PR labeling behavior is unchanged.

- README install section replaced by a generated `DIST-STATUS` banner stating the tool is source-only (no PyPI release yet) and offering both `pipx` and `uv` source installs.

- `ab-run --task/--repo` is now the deprecated two-session "simple
  mode"; the G3 suite harness is the default `ab-run` path (requires
  `--evals`, tasks defaulting to `<evals>/tasks`).
- `llms.txt` no longer states a hard-coded ecosystem size; the registry owns the count.
