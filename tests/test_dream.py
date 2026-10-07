"""devin-evals dream: generated DBs must parse + carry the labeled verdict."""

import json
from pathlib import Path

import pytest

from devin_evals.dream.cli import main
from devin_evals.dream.defects import DEFECTS, FAKE_AWS_KEY, UNIT_IDS
from devin_evals.dream.generate import ARCHETYPES
from devin_internals.commits import commit_references
from devin_internals.parsers import SessionsStore, StateVscdbStore
from devin_internals.schema import SchemaError


def test_unit_generates_all_and_parses(tmp_path):
    out = tmp_path / "u"
    assert main(["unit", "--out", str(out)]) == 0
    for did in UNIT_IDS:
        if did == "D06":
            continue  # drift canary is intentionally unparseable
        db = out / did.lower() / "sessions.db"
        assert db.is_file()
        exp = json.loads((db.parent / "expected.json").read_text())
        assert exp["defect"] == did and exp["expected"]
        with SessionsStore(db) as st:
            assert len(st.sessions()) == 1
            assert st.sessions()[0].id == exp["session_id"]
            assert st.message_nodes()


def test_d06_fails_schema_detection_loudly(tmp_path):
    out = tmp_path / "u"
    assert main(["unit", "--out", str(out), "--defect", "D06"]) == 0
    with pytest.raises(SchemaError):
        SessionsStore(out / "d06" / "sessions.db")


def test_d04_carries_fake_secret_in_tool_output(tmp_path):
    out = tmp_path / "u"
    main(["unit", "--out", str(out), "--defect", "D04"])
    with SessionsStore(out / "d04" / "sessions.db") as st:
        tcs = st.tool_call_state()
    blob = tcs[0].tool_call_update_json
    assert FAKE_AWS_KEY in blob


def test_d09_splits_secret_across_calls(tmp_path):
    from devin_evals.dream.defects import FAKE_AWS_KEY
    out = tmp_path / "u"
    main(["unit", "--out", str(out), "--defect", "D09"])
    with SessionsStore(out / "d09" / "sessions.db") as st:
        joined = "".join(
            tc.tool_call_update_json or "" for tc in st.tool_call_state())
    # neither payload alone is a full key; together they form one
    assert joined.count("AKIAIOSFOD") == 1
    assert FAKE_AWS_KEY[:10] in joined and FAKE_AWS_KEY[10:] in joined


def test_unknown_defect_is_usage_error(tmp_path):
    assert main(["unit", "--out", str(tmp_path), "--defect", "D99"]) == 2


def test_inject_scorecard(tmp_path):
    out = tmp_path / "i"
    assert main(["inject", "--out", str(out), "--n", "3"]) == 0
    card = json.loads((out / "expected.json").read_text())
    assert card["sessions"] == 6
    assert card["expected_blocks"] == {"D07": 3, "D08": 3}
    with SessionsStore(out / "sessions.db") as st:
        assert len(st.sessions()) == 6


def test_fleet_archetype_coverage_and_parse(tmp_path):
    out = tmp_path / "f"
    assert main(["fleet", "--out", str(out), "--sessions", "50",
                 "--seed", "7", "--manifest"]) == 0
    manifest = json.loads((out / "fleet.json").read_text())
    assert manifest["session_count"] == 50
    assert manifest["seed"] == 7
    assert {e["archetype"] for e in manifest["sessions"]} == set(ARCHETYPES)
    # every session carries the uniform flags
    for e in manifest["sessions"]:
        assert set(e["expected"]) >= {
            "produces_commit", "heavy_churn", "failed", "trivial"}
    with SessionsStore(out / "sessions.db") as st:
        sessions = st.sessions()
        assert len(sessions) == 50
        assert all(s.metadata and "archetype:" in s.metadata
                   for s in sessions)


def test_fleet_manifest_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    args = ["fleet", "--sessions", "40", "--seed", "1234", "--manifest"]
    assert main([*args, "--out", str(a)]) == 0
    assert main([*args, "--out", str(b)]) == 0
    assert (a / "fleet.json").read_bytes() == (b / "fleet.json").read_bytes()
    assert (a / "sessions.db").read_bytes() == (
        b / "sessions.db").read_bytes()


def test_fleet_commit_references_extract_shas(tmp_path):
    out = tmp_path / "f"
    assert main(["fleet", "--out", str(out), "--sessions", "50",
                 "--seed", "3", "--manifest"]) == 0
    manifest = json.loads((out / "fleet.json").read_text())
    producing = {e["session_id"]: e["expected"]["commit_shas"]
                 for e in manifest["sessions"]
                 if e["expected"]["produces_commit"]}
    assert producing  # coverage guarantee
    with SessionsStore(out / "sessions.db") as st:
        refs = commit_references(st.tool_call_state())
    by_session = {}
    for r in refs:
        by_session.setdefault(r.session_id, set()).add(r.sha)
    for sid, shas in producing.items():
        assert set(shas) <= by_session.get(sid, set())
    # commit URLs carry repo context
    assert any(r.repo_url and r.repo_url.startswith("github.com/dream-org/")
               for r in refs)


def test_fleet_heavy_churn_repeats_one_file(tmp_path):
    out = tmp_path / "f"
    main(["fleet", "--out", str(out), "--sessions", "50", "--seed", "5",
          "--manifest"])
    manifest = json.loads((out / "fleet.json").read_text())
    churners = {e["session_id"]: e["expected"]["churned_file"]
                for e in manifest["sessions"]
                if e["expected"]["heavy_churn"]}
    assert churners
    with SessionsStore(out / "sessions.db") as st:
        for sid, path in churners.items():
            blobs = " ".join(
                (tc.tool_call_json or "") + (tc.tool_call_update_json or "")
                for tc in st.tool_call_state(sid))
            assert blobs.count(path) >= 8  # same file touched repeatedly


def test_fleet_vscdb_bindings(tmp_path):
    out = tmp_path / "f"
    vdb = tmp_path / "state.vscdb"
    assert main(["fleet", "--out", str(out), "--sessions", "60",
                 "--seed", "9", "--manifest", "--vscdb", str(vdb)]) == 0
    manifest = json.loads((out / "fleet.json").read_text())
    bound = {e["session_id"]: e["gui_slug"] for e in manifest["sessions"]
             if "gui_slug" in e}
    assert bound
    with StateVscdbStore(vdb) as st:
        ws = st.list_prefix("windsurfSpace.sessionWorkspace/")
        rts = json.loads(st.get("windsurfSpace.resourceToSpace"))
        meta = json.loads(st.get("windsurfSpace.metadata"))
    slugs = {k.rsplit("/", 1)[-1] for k in ws}
    assert slugs == set(bound.values())
    # every slug resolves to a space, every space has lastAccessed
    uris = [u for uris in rts.values() for u in uris]
    assert {u.rsplit("/", 1)[-1] for u in uris} == slugs
    assert set(meta) == set(rts)


def test_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    main(["unit", "--out", str(a), "--defect", "D01"])
    main(["unit", "--out", str(b), "--defect", "D01"])
    assert (a / "d01" / "sessions.db").read_bytes() == (
        b / "d01" / "sessions.db").read_bytes()
