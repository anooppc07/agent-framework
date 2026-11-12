"""Task 5 docs tests (plan cases 36-37): the skill's entrypoint and hosted flow must say, and so
drive, the behavior the code enforces: ask with choices (preset first), wait for explicit
confirmation, build through the CLIs, create MAF agents, and stop at local files.
Meaning is tested, not isolated words. Both roots are checked when the second root exists.
Run from overlay/:  PYTHONPATH= python -m pytest tests -q
"""

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent.parent
ROOTS = [r for r in (SKILL, SKILL.parent.parent.parent / ".github" / "skills" / "foundry-agent") if r.exists()]


def text(root, rel):
    path = root / rel
    assert path.exists(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def sentences(doc):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\s*\n|\n[*\-] ", doc) if s.strip()]


def mentions(doc, *needles):
    return [s for s in sentences(doc) if all(n.lower() in s.lower() for n in needles)]


# --- SKILL.md: recommend, ask with choices, wait for the user ------------------------------------


def test_entrypoint_offers_a_preset_with_choices_and_an_override(root=SKILL):
    doc = text(root, "SKILL.md")
    assert mentions(doc, "preset", "override"), "SKILL.md must say a preset pre-fills answers that can be overridden"
    assert "examples/card-dispute/preset.json" in doc
    assert mentions(doc, "(Recommended)") or mentions(doc, "recommended", "first")


def test_entrypoint_drives_the_intake_cli_in_order(root=SKILL):
    doc = text(root, "SKILL.md")
    assert "overlay/intake.py" in doc
    positions = [doc.index(f"intake.py {c}") for c in ("questions", "build", "create")]
    assert positions == sorted(positions), "questions, then build, then create"


def test_entrypoint_makes_the_user_the_final_selector(root=SKILL):
    doc = text(root, "SKILL.md")
    assert mentions(doc, "recommend", "user", "select") or mentions(doc, "recommend", "wait", "user")
    gate = mentions(doc, "silence") + mentions(doc, "default", "not consent") + mentions(doc, "not consent")
    assert gate, "SKILL.md must say a recommendation, default or silence is not consent"
    assert mentions(doc, "--confirm-pattern") and mentions(doc, "--confirm-kind")
    assert mentions(doc, "only after") or mentions(doc, "never before"), "confirm flags are passed only after the user chooses"


def test_entrypoint_keeps_the_recommendation_separate_from_the_choice(root=SKILL):
    doc = text(root, "SKILL.md")
    assert "--recommend-pattern" in doc and mentions(doc, "recommend", "separate")


def test_entrypoint_lists_all_four_patterns_and_marks_two_design_only(root=SKILL):
    doc = text(root, "SKILL.md")
    for pid in ("single-agent", "router-specialists", "supervisor-worker", "agent-handoffs"):
        assert pid in doc
    assert mentions(doc, "supervisor-worker", "design-only") or mentions(doc, "design-only", "proposal")


def test_entrypoint_no_longer_calls_the_hosted_template_langgraph(root=SKILL):
    doc = text(root, "SKILL.md")
    assert "langgraph" not in doc.lower()
    assert mentions(doc, "agent framework") or mentions(doc, "maf")


# --- no deployment during creation ------------------------------------------------------------


@pytest.mark.parametrize("rel", ["SKILL.md", "hosted/FLOW.md"])
def test_creation_stops_at_local_files_and_never_deploys(rel, root=SKILL):
    doc = text(root, rel)
    assert mentions(doc, "never deploy") or mentions(doc, "does not deploy") or mentions(doc, "not deploy")
    assert mentions(doc, "separate", "deploy") or mentions(doc, "explicit", "deploy")
    for bad in (r"hand off to\s+`?azure:microsoft-foundry", r"run `?azd (up|deploy|provision)", r"\bazd up\b"):
        assert not re.search(bad, doc, re.I), f"{rel} still instructs a deploy step: {bad}"


def test_hosted_flow_has_no_deploy_handoff_step(root=SKILL):
    doc = text(root, "hosted/FLOW.md")
    steps = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", doc, re.M)
    assert steps, "hosted flow lost its numbered steps"
    assert not any("hand off" in s.lower() or "deploy" in s.lower() for s in steps), steps


# --- hosted/FLOW.md: MAF, managed sessions, one owner -----------------------------------------------


def test_hosted_flow_describes_maf_managed_sessions_and_no_custom_runtime_code(root=SKILL):
    doc = text(root, "hosted/FLOW.md")
    assert mentions(doc, "agent framework") or mentions(doc, "maf")
    assert mentions(doc, "session", "foundry") and mentions(doc, "preflight")
    assert mentions(doc, "custom", "session") or mentions(doc, "no custom", "protocol")
    assert "langgraph" not in doc.lower().replace("not langgraph", "")


def test_hosted_flow_names_one_creator_per_pattern(root=SKILL):
    doc = text(root, "hosted/FLOW.md")
    assert "router-specialists" in doc and "scaffold_overlay.py" in doc or "intake.py" in doc
    assert mentions(doc, "one creator") or mentions(doc, "single owner") or mentions(doc, "only one")


def test_hosted_flow_routes_router_creation_through_the_cli_not_manual_copying(root=SKILL):
    doc = text(root, "hosted/FLOW.md")
    section = doc[doc.index("router"):] if "router" in doc else ""
    assert "intake.py" in section and mentions(section, "do not", "copy") or mentions(section, "cli")


def test_hosted_flow_says_ai_generated_content_stays_local_and_no_secrets(root=SKILL):
    doc = text(root, "hosted/FLOW.md")
    assert mentions(doc, "no secrets") or mentions(doc, "secrets", "endpoints")


# --- references ---------------------------------------------------------------------------------


def test_references_exist_and_are_linked_from_the_entrypoint(root=SKILL):
    doc = text(root, "SKILL.md")
    assert "references/runtime-patterns.md" in doc
    ref = text(root, "references/runtime-patterns.md")
    for part in ("Session manager", "Ownership", "Unverified"):
        assert part in ref


def test_the_reference_separates_platform_defaults_from_application_duties_and_approval(root=SKILL):
    ref = text(root, "references/runtime-patterns.md")
    assert re.search(r"platform", ref, re.I) and re.search(r"application", ref, re.I)
    assert mentions(ref, "approval") or mentions(ref, "declar")


# --- the preset ---------------------------------------------------------------------------------


def test_the_preset_is_marked_proposed_and_names_no_policy(root=SKILL):
    preset = text(root, "examples/card-dispute/preset.json")
    assert "proposed-synthetic" in preset and "do not state bank policy" in preset.lower()


# --- both roots carry the same behavior ------------------------------------------------------------

BEHAVIOR_TESTS = [v for k, v in globals().items() if k.startswith("test_") and "root" in v.__code__.co_varnames[:1]]


# The .github copy is mirrored from .claude by Task 6. Until then it still holds the old hosted template, so
# these checks are expected to fail there. They are listed by name (measured, not guessed) and strict, so
# once the mirror lands an unexpected pass fails the run and this list must be emptied. Nothing else is excused.
UNMIRRORED_GITHUB = {
    "test_entrypoint_offers_a_preset_with_choices_and_an_override",
    "test_entrypoint_drives_the_intake_cli_in_order",
    "test_entrypoint_makes_the_user_the_final_selector",
    "test_entrypoint_keeps_the_recommendation_separate_from_the_choice",
    "test_entrypoint_lists_all_four_patterns_and_marks_two_design_only",
    "test_entrypoint_no_longer_calls_the_hosted_template_langgraph",
    "test_hosted_flow_has_no_deploy_handoff_step",
    "test_hosted_flow_describes_maf_managed_sessions_and_no_custom_runtime_code",
    "test_hosted_flow_names_one_creator_per_pattern",
    "test_hosted_flow_routes_router_creation_through_the_cli_not_manual_copying",
    "test_references_exist_and_are_linked_from_the_entrypoint",
    "test_the_reference_separates_platform_defaults_from_application_duties_and_approval",
    "test_the_preset_is_marked_proposed_and_names_no_policy",
}


def _expect_fail(root, name):
    return root.parent.parent.name == ".github" and name in UNMIRRORED_GITHUB


def _cases():
    out = []
    for root in ROOTS:
        for check in BEHAVIOR_TESTS:
            if check.__name__.startswith("test_creation_stops"):
                continue
            marks = [pytest.mark.xfail(strict=True, reason="mirrored from .claude by Task 6")]                 if _expect_fail(root, check.__name__) else []
            out.append(pytest.param(check, root, id=f"{check.__name__}-{root.parent.parent.name}", marks=marks))
    return out


@pytest.mark.parametrize("check,root", _cases())
def test_every_root_satisfies_the_same_behavior(check, root):
    check(root=root)
