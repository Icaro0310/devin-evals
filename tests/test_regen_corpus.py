"""EV-3 committed corpus: ``tools/regen-corpus.py`` drift gate.

The committed corpus (``corpus/evals/*.json`` + ``corpus/corpus.json``)
must always equal a fresh deterministic regeneration — the ``--check``
mode is the CI gate that enforces it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from devin_evals.cases import load_cases

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "regen-corpus.py"
CORPUS = REPO / "corpus"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=REPO,
    )


def _copy_committed_text(dest: Path) -> Path:
    """Copy only the committed (text) half of the corpus into ``dest``."""
    (dest / "evals").mkdir(parents=True)
    shutil.copy2(CORPUS / "corpus.json", dest / "corpus.json")
    for f in (CORPUS / "evals").glob("*.json"):
        shutil.copy2(f, dest / "evals" / f.name)
    return dest


# -- committed corpus sanity --------------------------------------------------


def test_committed_corpus_cases_load():
    cases = load_cases(CORPUS / "evals")
    assert len(cases) == 9
    assert all(c.checks for c in cases)
    # D03 exercises the EV-4 packs wiring end-to-end.
    d03 = next(c for c in cases if c.id == "golden-d03-verified-claim")
    graders = [ch.grader for ch in d03.checks]
    assert graders[:3] == ["exit_code", "not_contains", "no_secrets"]


def test_committed_manifest_is_seeded_and_synthetic():
    manifest = json.loads((CORPUS / "corpus.json").read_text(encoding="utf-8"))
    assert manifest["synthetic_only"] is True
    assert manifest["generator"] == "dream"
    assert manifest["seed"] == 0xDEE4
    assert len(manifest["cases"]) == 9


def test_committed_corpus_has_no_real_session_data():
    """Every committed case must point at a synthetic dream session."""
    for f in (CORPUS / "evals").glob("*.json"):
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["synthetic"] is True, f.name
        assert data["session_ref"].startswith("dream-"), f.name


# -- regen-corpus.py ----------------------------------------------------------


def test_check_passes_on_committed_corpus():
    rc = _run("--check")
    assert rc.returncode == 0, rc.stderr
    assert "corpus OK" in rc.stdout


def test_check_detects_drift(tmp_path):
    dest = _copy_committed_text(tmp_path / "corpus")
    target = dest / "evals" / "golden-d03-verified-claim.json"
    data = json.loads(target.read_text(encoding="utf-8"))
    data["description"] = "hand-edited after commit"
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    rc = _run("--check", "--corpus-dir", str(dest))
    assert rc.returncode == 1
    assert "golden-d03-verified-claim.json" in rc.stderr


def test_check_detects_missing_and_extra_files(tmp_path):
    dest = _copy_committed_text(tmp_path / "corpus")
    (dest / "evals" / "golden-d05-pii-in-prompt.json").unlink()
    (dest / "evals" / "hand-added.json").write_text("{}", encoding="utf-8")
    rc = _run("--check", "--corpus-dir", str(dest))
    assert rc.returncode == 1
    assert "regenerated but not committed" in rc.stderr
    assert "no longer generated" in rc.stderr


def test_check_fails_when_no_committed_corpus(tmp_path):
    rc = _run("--check", "--corpus-dir", str(tmp_path / "empty"))
    assert rc.returncode == 1
    assert "no committed corpus" in rc.stderr


def test_regen_reproduces_committed_bytes(tmp_path):
    """A from-scratch regen must equal the committed corpus byte-for-byte."""
    dest = tmp_path / "corpus"
    rc = _run("--corpus-dir", str(dest))
    assert rc.returncode == 0, rc.stderr
    committed = {f.name: f.read_bytes()
                 for f in (CORPUS / "evals").glob("*.json")}
    regenerated = {f.name: f.read_bytes()
                   for f in (dest / "evals").glob("*.json")}
    assert regenerated == committed
    assert (dest / "corpus.json").read_bytes() == (
        CORPUS / "corpus.json").read_bytes()
    # and the generated-only half materialized too
    assert (dest / "sessions.db").is_file()
    assert (dest / "sessions-d06.db").is_file()


def test_regen_cleans_stale_artifacts(tmp_path):
    dest = _copy_committed_text(tmp_path / "corpus")
    stale = dest / "evals" / "stale-leftover.json"
    stale.write_text("{}", encoding="utf-8")
    rc = _run("--corpus-dir", str(dest))
    assert rc.returncode == 0
    assert not stale.exists()


def test_regen_verify_flag(tmp_path):
    dest = tmp_path / "corpus"
    rc = _run("--verify", "--corpus-dir", str(dest))
    assert rc.returncode == 0, rc.stderr
    assert "9/9 matched, 0 known gap(s), 0 mismatch(es)" in rc.stdout
