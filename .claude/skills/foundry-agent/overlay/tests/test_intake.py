"""Intake tests (plan Task 5 dispatch cases, demo path): a preset pre-fills every answer, each choice can
be overridden, a recommendation or missing reply never writes files, and an explicit matching
confirmation runs only the matching template. Real files and modules; no Azure.
Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

OVERLAY = Path(__file__).resolve().parent.parent
SKILL = OVERLAY.parent
PRESET = SKILL / "examples" / "card-dispute" / "preset.json"
sys.path.insert(0, str(OVERLAY))

import agent_spec as S  # noqa: E402


def intake():
    try:
        import intake as mod
        return mod
    except ImportError as exc:
        pytest.fail(f"intake does not import: {exc}")


def preset():
    assert PRESET.exists(), "the card-dispute preset is missing"
    return intake().load_preset(PRESET)


def clean_env():
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


# --- the preset ---------------------------------------------------------------------------------


def test_preset_loads_and_is_marked_proposed_and_synthetic():
    p = preset()
    assert p["status"] == "proposed-synthetic" and p["defaults"]["pattern"] == "router-specialists"


def test_preset_holds_no_endpoints_secrets_or_policy_claims():
    text = PRESET.read_text(encoding="utf-8").lower()
    for marker in ("http://", "https://", "api-key", "secret", "subscription", "password:"):
        assert marker not in text
    instructions = " ".join(s["instructions"] for s in json.loads(text)["defaults"]["specialists"])
    assert "do not state bank policy" in instructions and "never ask for a full card number" in instructions


def test_missing_or_malformed_preset_raises_spec_error(tmp_path):
    with pytest.raises(S.SpecError, match="missing.json"):
        intake().load_preset(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(S.SpecError, match="bad.json"):
        intake().load_preset(bad)
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    with pytest.raises(S.SpecError, match="defaults"):
        intake().load_preset(empty)


# --- questions: choices with the preset first, owner free text ---------------------------------


def test_questions_are_batched_for_the_selection_prompt():
    batches = intake().questions(preset())
    assert batches and all(1 <= len(b) <= 4 for b in batches)
    ids = [q["id"] for b in batches for q in b]
    assert ids[0] == "pattern" and "owner" in ids and len(ids) == len(set(ids))


def test_each_choice_question_leads_with_the_preset_value_marked_recommended():
    p = preset()
    by_id = {q["id"]: q for b in intake().questions(p) for q in b}
    assert by_id["pattern"]["options"][0]["value"] == "router-specialists"
    assert by_id["pattern"]["options"][0]["label"].endswith("(Recommended)")
    assert by_id["model"]["options"][0]["value"] == p["defaults"]["model"]
    assert by_id["classification"]["options"][0]["value"] == "internal"
    for q in by_id.values():
        if q["kind"] == "choice":
            assert 2 <= len(q["options"]) <= 4
            assert sum(o["label"].endswith("(Recommended)") for o in q["options"]) == 1
            assert all({"value", "label", "description"} <= set(o) for o in q["options"])


def test_a_changed_preset_default_moves_to_the_front():
    p = preset()
    p["defaults"]["classification"] = "confidential"
    q = {x["id"]: x for b in intake().questions(p) for x in b}["classification"]
    assert q["options"][0]["value"] == "confidential" and q["options"][0]["label"].endswith("(Recommended)")


def test_owner_is_free_text_with_no_assumed_default():
    q = {x["id"]: x for b in intake().questions(preset()) for x in b}["owner"]
    assert q["kind"] == "text" and "options" not in q and "default" not in q


def test_unavailable_extras_are_not_offered(monkeypatch):
    import scaffold_overlay
    monkeypatch.setattr(scaffold_overlay, "EXTRAS_AVAILABLE", False)
    ids = [q["id"] for b in intake().questions(preset()) for q in b]
    assert "evals" not in ids and "pipeline" not in ids
    monkeypatch.setattr(scaffold_overlay, "EXTRAS_AVAILABLE", True)
    ids = [q["id"] for b in intake().questions(preset()) for q in b]
    assert "evals" in ids and "pipeline" in ids


# --- build_spec: preset defaults plus overrides ------------------------------------------------


def test_only_the_owner_is_needed_to_build_a_valid_runnable_spec():
    spec = intake().build_spec(preset(), {"owner": "payments-team"})
    parsed = S.parse_spec(spec)
    S.validate_for_mode(parsed, "create-new")
    assert parsed.name == "card-dispute-router" and parsed.owner == "payments-team"
    assert [s.name for s in parsed.specialists] == ["dispute-intake", "fraud-report"]


def test_answers_override_the_preset():
    spec = intake().build_spec(preset(), {"owner": "o", "classification": "confidential", "name": "my-agent",
                                          "model": "gpt-5.4"})
    assert (spec["classification"], spec["name"], spec["model"]) == ("confidential", "my-agent", "gpt-5.4")
    assert spec["pattern"] == "router-specialists"  # untouched defaults stay


def test_free_text_other_values_are_accepted_for_open_questions_only():
    spec = intake().build_spec(preset(), {"owner": "o", "name": "custom-name", "model": "my-deployment"})
    assert spec["name"] == "custom-name" and spec["model"] == "my-deployment"
    with pytest.raises(S.SpecError, match="classification"):
        intake().build_spec(preset(), {"owner": "o", "classification": "top-secret"})
    with pytest.raises(S.SpecError, match="pattern"):
        intake().build_spec(preset(), {"owner": "o", "pattern": "swarm"})


@pytest.mark.parametrize("answers", [{}, {"owner": ""}, {"owner": "   "}, {"owner": None}])
def test_missing_or_blank_owner_is_reported_by_name(answers):
    with pytest.raises(S.SpecError, match="owner"):
        intake().build_spec(preset(), answers)


def test_unknown_answer_keys_are_rejected_not_ignored():
    with pytest.raises(S.SpecError, match="unknown"):
        intake().build_spec(preset(), {"owner": "o", "colour": "blue"})


def test_choosing_single_agent_drops_the_specialists_and_keeps_the_rest():
    spec = intake().build_spec(preset(), {"owner": "o", "pattern": "single-agent"})
    assert spec["pattern"] == "single-agent" and "specialists" not in spec and "out_of_scope_examples" not in spec


def test_evals_yes_is_refused_clearly_while_generation_is_unavailable(monkeypatch):
    import scaffold_overlay
    monkeypatch.setattr(scaffold_overlay, "EXTRAS_AVAILABLE", False)
    with pytest.raises(S.SpecError, match="not available"):
        intake().build_spec(preset(), {"owner": "o", "evals": "yes"})
    monkeypatch.setattr(scaffold_overlay, "EXTRAS_AVAILABLE", True)
    assert intake().build_spec(preset(), {"owner": "o", "evals": "yes"})["evals_enabled"] is True


def test_pipeline_yes_without_evals_is_rejected(monkeypatch):
    import scaffold_overlay
    monkeypatch.setattr(scaffold_overlay, "EXTRAS_AVAILABLE", True)
    with pytest.raises(S.SpecError, match="pipeline"):
        intake().build_spec(preset(), {"owner": "o", "evals": "no", "pipeline": "yes"})


# --- dispatch: recommendation is not consent ---------------------------------------------------

CONFIRM = {"confirmed": True, "agent_kind": "hosted", "pattern": "router-specialists"}


def spec_for(**answers):
    return intake().build_spec(preset(), {"owner": "payments-team", **answers})


@pytest.mark.parametrize("confirmation", [None, {}, {"confirmed": False, "agent_kind": "hosted",
                                                     "pattern": "router-specialists"},
                                          {"confirmed": "yes", "agent_kind": "hosted", "pattern": "router-specialists"}])
def test_no_explicit_confirmation_writes_nothing(tmp_path, confirmation):
    out = tmp_path / "out"
    result = intake().dispatch(spec_for(), confirmation, out, recommendation={"pattern": "router-specialists"})
    assert result.action == "none" and result.written == () and not out.exists()
    assert "confirm" in result.reason.lower()


def test_a_selection_that_differs_from_the_spec_is_revalidated_not_run(tmp_path):
    out = tmp_path / "out"
    changed = {"confirmed": True, "agent_kind": "hosted", "pattern": "single-agent"}
    result = intake().dispatch(spec_for(), changed, out)
    assert result.action == "revalidate" and not out.exists()
    assert "single-agent" in result.reason and "router-specialists" in result.reason


def test_an_explicit_matching_confirmation_runs_only_the_router_template(tmp_path):
    out = tmp_path / "out"
    result = intake().dispatch(spec_for(), CONFIRM, out, recommendation={"pattern": "router-specialists",
                                                                         "why": "two independent topics"})
    assert result.action == "created" and (out / "main.py").exists() and result.written
    assert (out / "scaffold-metadata.json").exists(), "metadata was not written"
    meta = json.loads((out / "scaffold-metadata.json").read_text(encoding="utf-8"))
    assert meta["selection"]["confirmation"] == "user-confirmed"
    assert meta["recommendation"] == {"pattern": "router-specialists", "why": "two independent topics"}
    assert meta["recommendation"] is not meta["selection"]


def test_single_agent_confirmation_delegates_to_the_base_flow_and_writes_nothing(tmp_path):
    out = tmp_path / "out"
    result = intake().dispatch(spec_for(pattern="single-agent"),
                               {"confirmed": True, "agent_kind": "hosted", "pattern": "single-agent"}, out)
    assert result.action == "delegate-base-flow" and not out.exists()


@pytest.mark.parametrize("pattern", ["supervisor-worker", "agent-handoffs"])
def test_design_only_patterns_produce_a_proposal_not_code(tmp_path, pattern):
    out = tmp_path / "out"
    result = intake().dispatch(spec_for(pattern=pattern), {"confirmed": True, "agent_kind": "hosted",
                                                           "pattern": pattern}, out)
    assert result.action == "design-only"
    assert {p.name for p in out.rglob("*") if p.is_file()} == {"runtime-design.md", "runtime-contract.md",
                                                               "runtime-contract.json"}


def test_a_scaffold_error_is_reported_and_leaves_no_files(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "mine.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(S.SpecError, match="empty"):
        intake().dispatch(spec_for(), CONFIRM, out)
    assert [p.name for p in out.iterdir()] == ["mine.txt"]


# --- the CLI the skill flow calls --------------------------------------------------------------


def run_cli(tmp_path, *args):
    return subprocess.run([sys.executable, str(OVERLAY / "intake.py"), *args], cwd=tmp_path, env=clean_env(),
                          capture_output=True, text=True)


def test_cli_questions_prints_batches_as_json(tmp_path):
    r = run_cli(tmp_path, "questions", "--preset", str(PRESET))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "questions printed nothing"
    batches = json.loads(r.stdout)
    assert batches[0][0]["id"] == "pattern" and batches[0][0]["options"][0]["label"].endswith("(Recommended)")


def test_cli_build_then_create_end_to_end_outside_the_source_tree(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"owner": "payments-team", "classification": "confidential"}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    r = run_cli(tmp_path, "build", "--preset", str(PRESET), "--answers", str(answers), "--out", str(spec))
    assert r.returncode == 0, r.stderr
    assert spec.exists(), "build did not write the spec"
    assert json.loads(spec.read_text(encoding="utf-8"))["classification"] == "confidential"
    out = tmp_path / "agent"
    r = run_cli(tmp_path, "create", "--spec", str(spec), "--out", str(out), "--confirm-pattern",
                "router-specialists", "--confirm-kind", "hosted")
    assert r.returncode == 0, r.stderr
    assert (out / "main.py").exists() and "Nothing was deployed" in r.stdout
    assert (out / "preflight.py").exists(), "generated package is incomplete"
    pf = subprocess.run([sys.executable, "preflight.py", "main.py"], cwd=out, env=clean_env(),
                        capture_output=True, text=True)
    assert pf.returncode == 0 and pf.stdout.startswith("enabled")


def test_cli_create_without_the_confirm_flags_writes_nothing_and_says_why(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"owner": "payments-team"}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    assert run_cli(tmp_path, "build", "--preset", str(PRESET), "--answers", str(answers),
                   "--out", str(spec)).returncode == 0
    assert spec.exists(), "build did not write the spec"
    out = tmp_path / "agent"
    r = run_cli(tmp_path, "create", "--spec", str(spec), "--out", str(out))
    assert r.returncode == 2 and "confirm" in r.stderr.lower() and not out.exists()


def test_cli_errors_exit_two_with_a_message(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text("{}", encoding="utf-8")
    r = run_cli(tmp_path, "build", "--preset", str(PRESET), "--answers", str(answers), "--out", str(tmp_path / "s"))
    assert r.returncode == 2 and "owner" in r.stderr and not (tmp_path / "s").exists()


# --- demo fixes: the recommendation is recorded separately; the closing line is not repeated ----


def _built_spec(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"owner": "payments-team"}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    assert run_cli(tmp_path, "build", "--preset", str(PRESET), "--answers", str(answers),
                   "--out", str(spec)).returncode == 0
    assert spec.exists(), "build did not write the spec"
    return spec


def test_cli_records_the_recommendation_apart_from_the_users_selection(tmp_path):
    spec = _built_spec(tmp_path)
    out = tmp_path / "agent"
    r = run_cli(tmp_path, "create", "--spec", str(spec), "--out", str(out), "--confirm-kind", "hosted",
                "--confirm-pattern", "router-specialists", "--recommend-pattern", "single-agent",
                "--recommend-why", "simplest sufficient option")
    assert r.returncode == 0, r.stderr
    assert (out / "scaffold-metadata.json").exists()
    meta = json.loads((out / "scaffold-metadata.json").read_text(encoding="utf-8"))
    assert meta["recommendation"] == {"pattern": "single-agent", "why": "simplest sufficient option"}
    assert meta["selection"]["pattern"] == "router-specialists"
    assert meta["selection"]["confirmation"] == "user-confirmed"


def test_cli_without_a_recommendation_records_none_not_a_made_up_one(tmp_path):
    spec = _built_spec(tmp_path)
    out = tmp_path / "agent"
    assert run_cli(tmp_path, "create", "--spec", str(spec), "--out", str(out), "--confirm-kind", "hosted",
                   "--confirm-pattern", "router-specialists").returncode == 0
    assert (out / "scaffold-metadata.json").exists()
    assert json.loads((out / "scaffold-metadata.json").read_text(encoding="utf-8"))["recommendation"] is None


def test_cli_rejects_a_recommendation_with_no_pattern(tmp_path):
    spec = _built_spec(tmp_path)
    r = run_cli(tmp_path, "create", "--spec", str(spec), "--out", str(tmp_path / "a"), "--confirm-kind", "hosted",
                "--confirm-pattern", "router-specialists", "--recommend-why", "because")
    assert r.returncode == 2 and "recommend" in r.stderr.lower() and not (tmp_path / "a").exists()


def test_closing_message_states_nothing_was_deployed_exactly_once(tmp_path):
    spec = _built_spec(tmp_path)
    r = run_cli(tmp_path, "create", "--spec", str(spec), "--out", str(tmp_path / "agent"), "--confirm-kind", "hosted",
                "--confirm-pattern", "router-specialists")
    assert r.returncode == 0, r.stderr
    assert r.stdout.lower().count("nothing was deployed") == 1
