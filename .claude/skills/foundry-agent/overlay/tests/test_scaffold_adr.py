"""Router creation writes the decision records (ADRs) once, from the user's actual answers.

Real catalog, real renderer, real files; nothing is mocked. A reason is the user's own words or
"Reason not recorded." - it is never invented. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

OVERLAY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OVERLAY))

import agent_spec as S  # noqa: E402

NO_REASON = "Reason not recorded."
EXPECTED = ["0001-agent-type.md", "0002-model-runtime.md", "0003-data-access.md",
            "0004-behavior-boundaries.md", "0005-evaluation.md", "0006-container-sizing.md"]


def mod():
    try:
        import scaffold_overlay
        return scaffold_overlay
    except ImportError as exc:
        pytest.fail(f"scaffold_overlay does not import: {exc}")


def spec_dict(**over):
    d = {
        "name": "card-router", "description": "Routes card questions", "owner": "payments-team",
        "classification": "internal", "agent_kind": "hosted", "framework": "maf", "pattern": "router-specialists",
        "pattern_rationale": "two independent specialists", "model": "gpt-test-9",
        "hosted_session_manager": "enabled", "evals_enabled": False, "pipeline_enabled": False,
        "specialists": [{"name": "billing", "instructions": "Answer billing questions only.", "examples": ["q1"]},
                        {"name": "fraud", "instructions": "Handle fraud reports only.", "examples": ["q2"]}],
    }
    d.update(over)
    return d


def create(tmp_path, reasons=None, **over):
    out = tmp_path / "out"
    mod().scaffold(S.parse_spec(spec_dict(**over)), out, mode="create-new", reasons=reasons)
    return out


def adr(out, name):
    path = out / "docs" / "adr" / name
    assert path.exists(), f"{name} was not written; found {sorted(p.name for p in (out / 'docs' / 'adr').glob('*'))}"
    return path.read_text(encoding="utf-8")


# --- the records exist, once, in catalog order ---------------------------------------------------


def test_create_new_router_writes_the_six_hosted_records_in_order(tmp_path):
    out = create(tmp_path)
    names = sorted(p.name for p in (out / "docs" / "adr").glob("*.md"))
    assert names == EXPECTED


def test_records_are_part_of_the_written_file_list_and_the_package_stays_complete(tmp_path):
    out = tmp_path / "out"
    written = mod().scaffold(S.parse_spec(spec_dict()), out, mode="create-new")
    rels = {Path(p).resolve().relative_to(out.resolve()).as_posix() for p in written}
    assert {f"docs/adr/{n}" for n in EXPECTED} <= rels and "main.py" in rels


def test_records_are_excluded_from_the_deployed_package(tmp_path):
    out = create(tmp_path)
    assert (out / "docs" / "adr").is_dir(), "records must exist before checking they are excluded from the package"
    assert "docs/" in (out / ".agentignore").read_text(encoding="utf-8").splitlines()


# --- content comes from the spec, not from guesses -----------------------------------------------


def test_each_record_reflects_the_users_actual_answers(tmp_path):
    out = create(tmp_path)
    assert "hosted" in adr(out, "0001-agent-type.md").lower()
    assert "gpt-test-9" in adr(out, "0002-model-runtime.md")
    data = adr(out, "0003-data-access.md").lower()
    assert "no tools" in data or "tool-less" in data or "none" in data
    behavior = adr(out, "0004-behavior-boundaries.md")
    assert "Answer billing questions only." in behavior and "Handle fraud reports only." in behavior
    assert "Evals off" in adr(out, "0005-evaluation.md") or "off" in adr(out, "0005-evaluation.md").lower()
    assert "0.25" in adr(out, "0006-container-sizing.md")


def test_container_sizing_uses_the_spec_resources_when_given(tmp_path):
    out = create(tmp_path, resource={"cpu": "1", "memory": "2Gi"})
    text = adr(out, "0006-container-sizing.md")
    decision = text.split("## Decision")[1].split("## Options considered")[0]
    assert "1 CPU and 2Gi memory" in decision and "0.25" not in decision
    assert "**1 / 2Gi or 2 / 4Gi** (chosen)" in text


def test_evaluation_record_follows_the_evals_choice(tmp_path):
    import scaffold_overlay as sc
    original = sc.EXTRAS_AVAILABLE
    try:
        sc.EXTRAS_AVAILABLE = True
        out = tmp_path / "on"
        # the eval renderer is not wired in yet; an evals-on record must still be derivable from the spec
        ans = sc.adr_answers(S.parse_spec(spec_dict(evals_enabled=True)), {})
    finally:
        sc.EXTRAS_AVAILABLE = original
    assert "evaluation" in ans, "adr_answers must produce an evaluation answer"
    assert "on" in ans["evaluation"]["decision"].lower()
    assert ans["evaluation"]["chosen"] == "Evals on (default)"


# --- reasons: the user's own words, or the fixed phrase ------------------------------------------


def test_without_reasons_every_record_says_reason_not_recorded(tmp_path):
    out = create(tmp_path)
    for name in EXPECTED:
        assert NO_REASON in adr(out, name), name


def test_a_reason_the_user_gave_is_used_verbatim_and_only_for_its_record(tmp_path):
    reason = "We pin the model so evaluations stay comparable."
    out = create(tmp_path, reasons={"model-runtime": reason})
    assert reason in adr(out, "0002-model-runtime.md")
    for name in EXPECTED:
        if name != "0002-model-runtime.md":
            assert reason not in adr(out, name)
            assert NO_REASON in adr(out, name)


def test_a_blank_reason_is_treated_as_not_given(tmp_path):
    out = create(tmp_path, reasons={"model-runtime": "   ", "agent-type": ""})
    assert NO_REASON in adr(out, "0002-model-runtime.md") and NO_REASON in adr(out, "0001-agent-type.md")


def test_unknown_reason_keys_are_rejected_before_any_write(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(S.SpecError, match="reason"):
        mod().scaffold(S.parse_spec(spec_dict()), out, mode="create-new", reasons={"colour": "blue"})
    assert not out.exists()


def test_reasons_must_be_strings(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(S.SpecError, match="reason"):
        mod().scaffold(S.parse_spec(spec_dict()), out, mode="create-new", reasons={"model-runtime": 5})
    assert not out.exists()


# --- the other modes are unchanged ---------------------------------------------------------------


def test_overlay_and_design_only_write_no_decision_records(tmp_path):
    target = tmp_path / "legacy"
    target.mkdir()
    (target / "main.py").write_text("x", encoding="utf-8")
    mod().scaffold(S.parse_spec(spec_dict(framework="legacy-langgraph", hosted_session_manager="unknown")), target,
                   mode="overlay-existing")
    assert not (target / "docs" / "adr").exists()
    design = tmp_path / "design"
    mod().scaffold(S.parse_spec(spec_dict()), design, mode="design-only")
    assert not (design / "docs" / "adr").exists()


def test_records_never_contain_unresolved_placeholders(tmp_path):
    out = create(tmp_path)
    records = list((out / "docs" / "adr").glob("*.md"))
    assert len(records) == len(EXPECTED), "records must exist before checking them"
    for p in records:
        assert not re.search(r"\{\{[A-Z][A-Z0-9_]*\}\}", p.read_text(encoding="utf-8")), p.name


# --- CLI: the user's reasons flow through intake create -----------------------------------------


def test_cli_passes_reasons_through_and_records_them(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(spec_dict()), encoding="utf-8")
    reasons = tmp_path / "reasons.json"
    reasons.write_text(json.dumps({"behavior-boundaries": "Compliance asked for a hard scope."}), encoding="utf-8")
    out = tmp_path / "agent"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, str(OVERLAY / "intake.py"), "create", "--spec", str(spec), "--out", str(out),
                        "--confirm-kind", "hosted", "--confirm-pattern", "router-specialists",
                        "--reasons", str(reasons)], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Compliance asked for a hard scope." in adr(out, "0004-behavior-boundaries.md")
    assert NO_REASON in adr(out, "0001-agent-type.md")


def test_cli_rejects_an_unreadable_reasons_file_without_writing(tmp_path):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(spec_dict()), encoding="utf-8")
    out = tmp_path / "agent"
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([sys.executable, str(OVERLAY / "intake.py"), "create", "--spec", str(spec), "--out", str(out),
                        "--confirm-kind", "hosted", "--confirm-pattern", "router-specialists",
                        "--reasons", str(tmp_path / "missing.json")], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "missing.json" in r.stderr and not out.exists()
