"""Q0 in the preset fast path: the agent kind (prompt or hosted) is asked first, recommended with a reason,
and a prompt answer stops the preset path instead of silently producing a hosted agent.
Real files and modules; nothing is deployed. Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

OVERLAY = Path(__file__).resolve().parent.parent
SKILL = OVERLAY.parent
PRESET = SKILL / "examples" / "card-dispute" / "preset.json"
sys.path.insert(0, str(OVERLAY))

import agent_spec as S  # noqa: E402
import intake  # noqa: E402


def preset():
    return intake.load_preset(PRESET)


def kind_question(p):
    assert hasattr(intake, "kind_question"), "intake.kind_question does not exist"
    return intake.kind_question(p)


def run_cli(tmp_path, *args):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, str(OVERLAY / "intake.py"), *args], cwd=tmp_path, env=env,
                          capture_output=True, text=True)


# --- the question itself -------------------------------------------------------------------------


def test_q0_offers_hosted_and_prompt_with_hosted_recommended_first():
    q = kind_question(preset())
    assert q["id"] == "agent_kind" and q["kind"] == "choice"
    assert [o["value"] for o in q["options"]] == ["hosted", "prompt"]
    assert q["options"][0]["label"].endswith("(Recommended)") and not q["options"][1]["label"].endswith("(Recommended)")


def test_q0_explains_why_hosted_is_recommended_and_what_prompt_means():
    opts = {o["value"]: o["description"].lower() for o in kind_question(preset())["options"]}
    assert "code" in opts["hosted"] and "router" in opts["hosted"]
    assert "no code" in opts["prompt"] and "prompt flow" in opts["prompt"]


def test_q0_follows_the_preset_default_rather_than_a_hardcoded_order():
    p = preset()
    p["defaults"]["agent_kind"] = "prompt"
    q = kind_question(p)
    assert q["options"][0]["value"] == "prompt" and q["options"][0]["label"].endswith("(Recommended)")


def test_q0_is_asked_separately_so_the_question_batches_are_unchanged():
    ids = [q["id"] for b in intake.questions(preset()) for q in b]
    assert "agent_kind" not in ids and ids[0] == "pattern"


def test_the_preset_declares_the_q0_question_and_defaults_to_hosted():
    p = preset()
    assert "agent_kind" in p["questions"] and p["defaults"]["agent_kind"] == "hosted"


# --- build_spec: hosted proceeds, prompt stops ----------------------------------------------------


def test_choosing_hosted_builds_the_same_spec_as_not_answering():
    explicit = intake.build_spec(preset(), {"owner": "o", "agent_kind": "hosted"})
    omitted = intake.build_spec(preset(), {"owner": "o"})
    assert explicit == omitted and explicit["agent_kind"] == "hosted"


def test_choosing_prompt_stops_the_preset_path_and_points_to_the_prompt_flow():
    with pytest.raises(S.SpecError) as e:
        intake.build_spec(preset(), {"owner": "o", "agent_kind": "prompt"})
    msg = str(e.value)
    assert "prompt/FLOW.md" in msg and "router" in msg.lower()


@pytest.mark.parametrize("bad", ["container", "", "  ", None, 3, ["hosted"]], ids=repr)
def test_an_unknown_or_malformed_kind_is_rejected_by_name(bad):
    with pytest.raises(S.SpecError, match="agent_kind") as e:
        intake.build_spec(preset(), {"owner": "o", "agent_kind": bad})
    assert "unknown key" not in str(e.value), "agent_kind is a real answer; a bad value is not an unknown key"
    assert "hosted" in str(e.value) and "prompt" in str(e.value), "the message must list the valid choices"


def test_a_prompt_answer_never_yields_a_hosted_spec():
    try:
        spec = intake.build_spec(preset(), {"owner": "o", "agent_kind": "prompt"})
    except S.SpecError:
        return
    pytest.fail(f"a prompt answer produced a spec instead of stopping: {spec.get('agent_kind')}")


def test_the_unknown_key_message_lists_agent_kind_as_a_valid_answer():
    with pytest.raises(S.SpecError, match="agent_kind"):
        intake.build_spec(preset(), {"owner": "o", "colour": "blue"})


# --- the confirmation still has to match the kind -------------------------------------------------


def test_confirming_a_different_kind_than_the_spec_is_revalidated_not_run(tmp_path):
    spec = intake.build_spec(preset(), {"owner": "o"})
    out = tmp_path / "out"
    result = intake.dispatch(spec, {"confirmed": True, "agent_kind": "prompt", "pattern": "router-specialists"}, out)
    assert result.action == "revalidate" and not out.exists()
    assert "prompt" in result.reason and "hosted" in result.reason


# --- the CLI the skill calls -----------------------------------------------------------------------


def test_cli_kind_prints_the_q0_question_as_json(tmp_path):
    r = run_cli(tmp_path, "kind", "--preset", str(PRESET))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "kind printed nothing"
    q = json.loads(r.stdout)
    assert q["id"] == "agent_kind" and q["options"][0]["value"] == "hosted"


def test_cli_build_with_prompt_exits_two_names_the_prompt_flow_and_writes_nothing(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"owner": "o", "agent_kind": "prompt"}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    r = run_cli(tmp_path, "build", "--preset", str(PRESET), "--answers", str(answers), "--out", str(spec))
    assert r.returncode == 2 and "prompt/FLOW.md" in r.stderr and not spec.exists()


def test_cli_build_with_hosted_succeeds(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps({"owner": "o", "agent_kind": "hosted"}), encoding="utf-8")
    spec = tmp_path / "spec.json"
    r = run_cli(tmp_path, "build", "--preset", str(PRESET), "--answers", str(answers), "--out", str(spec))
    assert r.returncode == 0, r.stderr
    assert spec.exists(), "build did not write the spec"
    assert json.loads(spec.read_text(encoding="utf-8"))["agent_kind"] == "hosted"


# --- SKILL.md tells the flow to ask Q0 first on the fast path -----------------------------------------

DOC = (SKILL / "SKILL.md").read_text(encoding="utf-8")
FAST = DOC[DOC.index("## Fast path"):DOC.index("## Patterns")]


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\s*\n|\n\d\. |\n[*\-] ", text) if s.strip()]


def mentions(text, *needles):
    return [s for s in sentences(text) if all(n.lower() in s.lower() for n in needles)]


def test_fast_path_asks_the_agent_kind_before_anything_else():
    assert "intake.py kind" in FAST and "intake.py questions" in FAST
    assert FAST.index("intake.py kind") < FAST.index("intake.py questions")
    assert mentions(FAST, "prompt", "hosted") and mentions(FAST, "first")


def test_fast_path_hands_a_prompt_choice_to_the_prompt_flow_and_never_substitutes():
    assert mentions(FAST, "prompt", "prompt/FLOW.md")
    assert mentions(FAST, "never", "substitute") or mentions(FAST, "not", "silently")


def test_fast_path_says_why_hosted_is_recommended_for_the_preset():
    assert mentions(FAST, "router", "code") or mentions(FAST, "hosted", "recommended", "router")
