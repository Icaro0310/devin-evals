"""Golden-case corpus (EV-3): generation, determinism, verify gate.

The corpus is synthetic by construction — no assertion below ever touches
a real sessions.db.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from devin_internals.schema import UnknownSchemaVersionError
from devin_internals.parsers.sessions import SessionsStore

from devin_evals.cases import load_cases
from devin_evals.cli import main
from devin_evals.corpus import (
    DEFECT_ORDER,
    GOLDEN_CASES,
    corpus_specs,
    generate_corpus,
    verify_corpus,
)
from devin_evals.graders import _SECRET_PATTERNS
from devin_evals.runner import _message_text, run_evals

EXPECTED_MATCHES = {"D01", "D02", "D03", "D04", "D05", "D06", "D07",
                    "D08", "D09", "D10"}
EXPECTED_GAPS: set[str] = set()


@pytest.fixture
def corpus_dir(tmp_path: Path) -> Path:
    out = tmp_path / "corpus"
    generate_corpus(out)
    return out


def _db_dump(path: Path) -> list[str]:
    return list(sqlite3.connect(path).iterdump())


# -- generation --------------------------------------------------------------


def test_generate_layout_and_loadable_cases(corpus_dir: Path):
    assert (corpus_dir / "corpus.json").is_file()
    assert (corpus_dir / "sessions.db").is_file()
    assert (corpus_dir / "sessions-d06.db").is_file()
    cases = load_cases(corpus_dir / "evals")
    assert len(cases) == len(DEFECT_ORDER)
    assert {c.id for c in cases} == {g.case_id for g in GOLDEN_CASES.values()}


def test_manifest_is_synthetic_and_seeded(corpus_dir: Path):
    manifest = json.loads((corpus_dir / "corpus.json").read_text())
    assert manifest["synthetic_only"] is True
    assert manifest["generator"] == "dream"
    assert isinstance(manifest["seed"], int)
    assert len(manifest["cases"]) == 10
    assert set(manifest["known_gaps"]) == EXPECTED_GAPS


def test_dream_specs_cover_all_defects():
    specs = corpus_specs()
    assert [s.defect_id for s in specs] == list(DEFECT_ORDER)
    assert sum(1 for s in specs if s.schema_version_override) == 1  # D06


# -- determinism -------------------------------------------------------------


def test_corpus_is_deterministic(tmp_path: Path):
    a, b = tmp_path / "a", tmp_path / "b"
    generate_corpus(a)
    generate_corpus(b)
    assert (a / "corpus.json").read_bytes() == (b / "corpus.json").read_bytes()
    for f in sorted((a / "evals").glob("*.json")):
        assert f.read_bytes() == (b / "evals" / f.name).read_bytes()
    for db in ("sessions.db", "sessions-d06.db"):
        assert _db_dump(a / db) == _db_dump(b / db)


def test_generated_cases_contain_no_secret_literals(corpus_dir: Path):
    """Eval JSON is text and must survive the repo's own secrets-scan."""
    for f in (corpus_dir / "evals").glob("*.json"):
        text = f.read_text(encoding="utf-8")
        for category, regexes in _SECRET_PATTERNS.items():
            for rx in regexes:
                assert not rx.findall(text), f"{f.name}: {category} pattern"


# -- verify ------------------------------------------------------------------


def test_verify_matches_expressible_verdicts(corpus_dir: Path):
    report = verify_corpus(corpus_dir)
    by_defect = {c["defect"]: c for c in report["cases"]}
    for did in EXPECTED_MATCHES:
        c = by_defect[did]
        assert c["result"] == "match", (did, c)
        assert c["actual_status"] == c["expected_status"]
    for did in EXPECTED_GAPS:
        c = by_defect[did]
        assert c["result"] == "gap", (did, c)
        assert c["expected_status"] == "fail"
        assert c["actual_status"] == "pass"
        assert c["known_gap"]
    s = report["summary"]
    assert s["matched"] == len(EXPECTED_MATCHES)
    assert s["gaps"] == len(EXPECTED_GAPS)
    assert s["mismatched"] == 0
    assert s["ok"] is True


def test_verify_strict_passes_with_no_gaps(corpus_dir: Path):
    report = verify_corpus(corpus_dir, strict=True)
    assert report["summary"]["ok"] is True
    assert report["summary"]["mismatched"] == 0


def test_verify_mismatch_is_detected(corpus_dir: Path):
    """Flip an expectation: verify must report a mismatch, not a gap."""
    p = corpus_dir / "evals" / "golden-d03-verified-claim.json"
    data = json.loads(p.read_text())
    data["expected_status"] = "fail"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    report = verify_corpus(corpus_dir)
    d03 = next(c for c in report["cases"] if c["defect"] == "D03")
    assert d03["result"] == "mismatch"
    assert report["summary"]["mismatched"] == 1
    assert report["summary"]["ok"] is False


def test_d06_db_is_refused_loudly(corpus_dir: Path):
    """The drift canary db must raise at open time — that IS the verdict."""
    with pytest.raises(UnknownSchemaVersionError):
        SessionsStore(corpus_dir / "sessions-d06.db")
    report = verify_corpus(corpus_dir)
    d06 = next(c for c in report["cases"] if c["defect"] == "D06")
    assert d06["actual_status"] == "error"
    assert "refused" in d06["detail"]


def test_verify_missing_corpus_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="corpus generate"):
        verify_corpus(tmp_path / "nope")


def test_verify_writes_report(corpus_dir: Path, tmp_path: Path):
    out = tmp_path / "vout"
    verify_corpus(corpus_dir, out_dir=out)
    report = json.loads((out / "verify-report.json").read_text())
    assert report["summary"]["total"] == 10


# -- the corpus dbs grade as advertised under plain `run` --------------------


def test_main_db_replays_with_run_evals(corpus_dir: Path):
    """Corpus cases stay valid inputs for the regular runner."""
    report = run_evals(corpus_dir / "evals", corpus_dir / "sessions.db")
    by_id = {c["id"]: c["status"] for c in report["cases"]}
    assert by_id["golden-d03-verified-claim"] == "pass"
    assert by_id["golden-d01-claim-without-evidence"] == "fail"
    # D06's session lives in its own drifted db -> unknown ref -> error.
    assert by_id["golden-d06-schema-drift"] == "error"


def test_message_text_reads_content_key():
    """dream/ACP blobs store text under 'content', not 'text'."""
    blob = json.dumps({"role": "assistant", "content": "ran the tests"})
    assert _message_text(blob) == "ran the tests"
    blob2 = json.dumps({"role": "agent", "text": "hello"})
    assert _message_text(blob2) == "hello"
    assert _message_text("not json") == "not json"


# -- CLI ---------------------------------------------------------------------


def test_cli_generate_and_verify(tmp_path: Path, capsys):
    out = tmp_path / "corpus"
    assert main(["corpus", "generate", "--out", str(out)]) == 0
    assert "golden case(s)" in capsys.readouterr().out
    assert main(["corpus", "verify", "--corpus", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "MATCH" in stdout and "10/10" in stdout
    # strict passes now that D05/D07/D09 gaps are closed by real graders
    assert main(["corpus", "verify", "--corpus", str(out), "--strict"]) == 0


def test_cli_verify_missing_corpus_returns_2(tmp_path: Path, capsys):
    rc = main(["corpus", "verify", "--corpus", str(tmp_path / "nope")])
    assert rc == 2
    assert "not a generated corpus" in capsys.readouterr().err


def test_cli_generate_uses_absorbed_dream(tmp_path: Path, capsys):
    out = tmp_path / "c"
    rc = main(["corpus", "generate", "--out", str(out)])
    assert rc == 0
    assert "generator=dream" in capsys.readouterr().out
