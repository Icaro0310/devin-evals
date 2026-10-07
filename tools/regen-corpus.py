#!/usr/bin/env python3
"""Regenerate the committed golden corpus (``corpus/``) — EV-3.

The corpus is *versioned*: ``corpus/evals/*.json`` and ``corpus/corpus.json``
are committed; the generated ``sessions*.db`` files are not (they are
rebuilt in place — ``*.db`` is gitignored and committing binaries is
against repo policy anyway).

Usage::

    python tools/regen-corpus.py             # rebuild corpus/ in place
    python tools/regen-corpus.py --check     # CI gate: exit 1 if committed
                                             # corpus != regenerated output
    python tools/regen-corpus.py --verify    # rebuild, then run corpus verify

Determinism: the recorded ``seed`` in ``corpus/corpus.json`` is reused,
so a rebuild is byte-identical unless the generator code or the defect
catalogue changed — which is exactly what ``--check`` catches. The
generator is ``devin_evals.dream`` (absorbed from devin-dream in P4).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from devin_evals.corpus import (  # noqa: E402
    DEFAULT_SEED,
    EVALS_DIRNAME,
    MANIFEST_NAME,
    generate_corpus,
    verify_corpus,
)

DEFAULT_CORPUS_DIR = REPO_ROOT / "corpus"


def _recorded_seed(corpus_dir: Path) -> int:
    """Seed recorded in the committed manifest (if present)."""
    manifest_path = corpus_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return DEFAULT_SEED
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return DEFAULT_SEED
    seed = manifest.get("seed")
    return seed if isinstance(seed, int) else DEFAULT_SEED


def _text_artifacts(corpus_dir: Path) -> dict[str, bytes]:
    """The committed half of a corpus: manifest + eval case files."""
    out: dict[str, bytes] = {}
    manifest = corpus_dir / MANIFEST_NAME
    if manifest.is_file():
        out[MANIFEST_NAME] = manifest.read_bytes()
    evals_dir = corpus_dir / EVALS_DIRNAME
    if evals_dir.is_dir():
        for p in sorted(evals_dir.glob("*.json")):
            out[f"{EVALS_DIRNAME}/{p.name}"] = p.read_bytes()
    return out


def _clean(corpus_dir: Path) -> None:
    """Remove previously generated artifacts so a rebuild is exact."""
    manifest = corpus_dir / MANIFEST_NAME
    if manifest.is_file():
        manifest.unlink()
    evals_dir = corpus_dir / EVALS_DIRNAME
    if evals_dir.is_dir():
        for p in evals_dir.glob("*.json"):
            p.unlink()
    for p in corpus_dir.glob("sessions*.db"):
        p.unlink()


def regenerate(corpus_dir: Path, seed: int) -> dict:
    """Rebuild the corpus in place; return the manifest."""
    corpus_dir.mkdir(parents=True, exist_ok=True)
    _clean(corpus_dir)
    return generate_corpus(corpus_dir, seed=seed)


def check(corpus_dir: Path, seed: int) -> list[str]:
    """Diff committed text artifacts against a fresh regeneration."""
    committed = _text_artifacts(corpus_dir)
    if not committed:
        return [f"{corpus_dir}: no committed corpus found — "
                f"run {Path(__file__).name} first"]
    with tempfile.TemporaryDirectory(prefix="devin-evals-corpus-") as tmp:
        generate_corpus(tmp, seed=seed)
        regenerated = _text_artifacts(Path(tmp))
    drift: list[str] = []
    for name in sorted(set(committed) | set(regenerated)):
        if name not in committed:
            drift.append(f"{name}: regenerated but not committed")
        elif name not in regenerated:
            drift.append(f"{name}: committed but no longer generated")
        elif committed[name] != regenerated[name]:
            drift.append(f"{name}: committed content differs from regenerated")
    return drift


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="regen-corpus",
        description="Regenerate the committed golden corpus (corpus/) "
        "deterministically, or --check that it is in sync.",
    )
    ap.add_argument(
        "--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR,
        help="corpus directory (default: %(default)s)")
    ap.add_argument(
        "--seed", type=int, default=None,
        help="override the seed recorded in corpus.json")
    ap.add_argument(
        "--check", action="store_true",
        help="do not write; fail if the committed corpus diverges from "
        "regenerated output (CI gate)")
    ap.add_argument(
        "--verify", action="store_true",
        help="after regenerating, run `corpus verify` against the corpus")
    args = ap.parse_args(argv)

    corpus_dir = args.corpus_dir.resolve()
    seed = args.seed if args.seed is not None else _recorded_seed(corpus_dir)

    if args.check:
        try:
            drift = check(corpus_dir, seed)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if drift:
            print(f"corpus drift detected in {corpus_dir}:", file=sys.stderr)
            for d in drift:
                print(f"  {d}", file=sys.stderr)
            print("run `python tools/regen-corpus.py` and commit the result",
                  file=sys.stderr)
            return 1
        n = len(_text_artifacts(corpus_dir))
        print(f"corpus OK: {n} committed file(s) match regenerated output "
              f"(seed={seed})")
        return 0

    try:
        manifest = regenerate(corpus_dir, seed)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"regenerated {len(manifest['cases'])} golden case(s) in "
          f"{corpus_dir} (seed={seed})")

    if args.verify:
        report = verify_corpus(corpus_dir)
        s = report["summary"]
        print(f"verify: {s['matched']}/{s['total']} matched, "
              f"{s['gaps']} known gap(s), {s['mismatched']} mismatch(es)")
        return 0 if s["ok"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
