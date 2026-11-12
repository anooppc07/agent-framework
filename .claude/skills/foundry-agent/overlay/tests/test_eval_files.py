"""eval_files(): the in-memory counterpart of render_eval_tree, so the scaffold can merge every generated
file into one all-or-nothing write. It must touch nothing on disk and agree exactly with what
render_eval_tree writes. Real modules and files only. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_spec as S  # noqa: E402
import evaluation_assets as E  # noqa: E402


def spec(**over):
    d = {"name": "card-router", "description": "d", "owner": "o", "classification": "internal", "agent_kind": "hosted",
         "framework": "maf", "pattern": "router-specialists", "pattern_rationale": "r", "model": "m",
         "hosted_session_manager": "enabled", "evals_enabled": True,
         "specialists": [{"name": "billing", "instructions": "x", "examples": ["Why was I charged twice?"]},
                         {"name": "fraud", "instructions": "y", "examples": ["I did not make this purchase"]}],
         "out_of_scope_examples": ["What is the weather?"]}
    d.update(over)
    return S.parse_spec(d)


def files(s):
    assert hasattr(E, "eval_files"), "evaluation_assets.eval_files does not exist"
    return E.eval_files(s)


def test_eval_files_returns_relative_path_to_content_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = files(spec())
    assert got, "an evals-enabled router must produce files"
    assert {"evals/evals.yaml", "evals/tests.yaml", "evals/datasets/baseline.jsonl", "evals/datasets/ci-smoke.jsonl",
            "evals/run_release_evaluation.py"} <= set(got)
    assert list(tmp_path.iterdir()) == [], "eval_files must not touch the disk"
    assert all(isinstance(v, (str, bytes)) for v in got.values())


def test_eval_files_matches_exactly_what_render_eval_tree_writes(tmp_path):
    s = spec()
    expected = files(s)
    E.render_eval_tree(s, tmp_path)
    on_disk = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert set(on_disk) == set(expected)
    for rel, data in expected.items():
        assert on_disk[rel] == (data.encode("utf-8") if isinstance(data, str) else data), rel


def test_eval_files_is_empty_when_evals_are_disabled():
    assert files(spec(evals_enabled=False)) == {}


def test_eval_files_raises_the_same_errors_before_anything_is_produced():
    with pytest.raises(S.SpecError):
        files(spec(specialists=[{"name": "billing", "instructions": "x", "examples": []},
                                {"name": "fraud", "instructions": "y", "examples": []}], out_of_scope_examples=[]))


def test_eval_files_includes_the_router_runner_only_for_routers():
    router = files(spec())
    assert any("routing" in k for k in router)
    single = files(spec(pattern="single-agent", specialists=[], out_of_scope_examples=[]))
    assert not any("routing" in k for k in single)
