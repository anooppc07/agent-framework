"""Offline tests for ADR generation. Real catalog, template and flow files; no mocks.

Run from the adr/ folder:  python -m pytest tests/ -v
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

import render_adr as adr

ADR_DIR = Path(__file__).resolve().parent.parent
SKILL_DIR = ADR_DIR.parent
DATE = "2026-11-10"
QUESTIONNAIRE = [
    "agent-type", "model-runtime", "data-access", "behavior-boundaries",
    "evaluation", "pipeline", "container-sizing",
]
DEPLOY_TIME = {"agent-identity-role", "existing-project-reuse"}
NO_REASON = "Reason not recorded."


def _answers(reason=None):
    return {s: {"decision": f"decision for {s}", "reason": reason} for s in QUESTIONNAIRE}


def _render(agent_type="hosted", pipeline=True, reason=None):
    return adr.render_all(agent_type, "demo-agent", DATE, _answers(reason), pipeline=pipeline)


def _slugs(agent_type, pipeline=True):
    return [slug for _, slug in adr.plan(agent_type, pipeline)]


# 1. empty / null input: the source files are present and non-empty -------------------------------


@pytest.mark.parametrize("name", ["TEMPLATE.md", "CATALOG.md"])
def test_source_files_exist_and_are_non_empty(name):
    assert (ADR_DIR / name).read_text(encoding="utf-8").strip()


# 2. catalog headings -----------------------------------------------------------------------------


def test_catalog_entries_match_headings_without_duplicates():
    text = (ADR_DIR / "CATALOG.md").read_text(encoding="utf-8")
    headings = re.findall(r"^#{2,3} ([a-z]+(?:-[a-z]+)*) \((?:alone|together)", text, re.M)
    assert len(headings) == len(set(headings)), "duplicate catalog heading"
    assert set(adr.catalog()) == set(headings)
    assert set(headings) == set(QUESTIONNAIRE) | DEPLOY_TIME


# 3. record sets per agent type -------------------------------------------------------------------


def test_prompt_and_hosted_record_sets():
    prompt, hosted = _slugs("prompt"), _slugs("hosted")
    assert len(prompt) == 6 and set(prompt) == set(QUESTIONNAIRE) - {"container-sizing"}
    assert len(hosted) == 6 and set(hosted) == set(QUESTIONNAIRE) - {"pipeline"}


# 4. numbering boundaries -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent_type, pipeline, count",
    [("prompt", True, 6), ("prompt", False, 5), ("hosted", True, 6), ("hosted", False, 6)],
)
def test_numbers_are_contiguous_and_agent_type_is_first(agent_type, pipeline, count):
    plan = adr.plan(agent_type, pipeline)
    assert [n for n, _ in plan] == [f"{i:04d}" for i in range(1, count + 1)]
    assert plan[0] == ("0001", "agent-type")


# 5. every template token is filled ---------------------------------------------------------------


def test_rendered_records_have_no_unfilled_tokens():
    files = _render()
    assert files, "nothing rendered"
    for name, text in files.items():
        assert "{{" not in text, f"{name} still has a placeholder"
        assert not (set(re.findall(r"\{\{([A-Z]+)\}\}", text))), name


# 6. error path: the reason is never invented -----------------------------------------------------


def _reason_section(text):
    return re.search(r"## Reason\n\n(.*?)\n\n## Consequences", text, re.S).group(1)


@pytest.mark.parametrize("reason", [None, "", "   "])
def test_missing_reason_is_stated_not_invented(reason):
    files = _render(reason=reason)
    assert len(files) == 6, "expected the full hosted set"
    for name, text in files.items():
        assert _reason_section(text) == NO_REASON, name


def test_given_reason_is_used_verbatim():
    files = _render(reason="Because the data lives in Fabric.")
    assert len(files) == 6, "expected the full hosted set"
    for name, text in files.items():
        assert _reason_section(text) == "Because the data lives in Fabric.", name


# 7. related links resolve ------------------------------------------------------------------------


def test_related_links_point_to_records_in_the_same_set():
    files = _render("prompt")
    assert files
    for name, text in files.items():
        related = re.search(r"^- Related: (.*)$", text, re.M).group(1)
        if name.endswith("-agent-type.md"):
            assert related == "None", name
            continue
        links = [x.strip() for x in related.split(",")]
        assert "0001-agent-type.md" in links, name
        assert all(link in files for link in links), (name, links)


# 8. deploy-time records stay out of the questionnaire step ---------------------------------------


def test_deploy_time_records_are_not_in_the_questionnaire_output():
    for agent_type in ("prompt", "hosted"):
        files = _render(agent_type)
        assert len(files) == 6, "expected a full record set"
        assert not DEPLOY_TIME & set(_slugs(agent_type))
        assert not any(s in n for n in files for s in DEPLOY_TIME)
    cat = adr.catalog()
    assert all(cat[s]["deploy_time"] is True for s in DEPLOY_TIME)
    assert all(cat[s]["deploy_time"] is False for s in QUESTIONNAIRE)


# 9. flows list exactly the slugs the catalog assigns ---------------------------------------------


@pytest.mark.parametrize("agent_type", ["prompt", "hosted"])
def test_flow_lists_the_catalog_slugs_for_its_type(agent_type):
    text = (SKILL_DIR / agent_type / "FLOW.md").read_text(encoding="utf-8")
    step = re.search(r"\*\*(?:Write decision records|Decision records)\.\*\*(.*?)Skip", text, re.S)
    assert step, "flow has no decision-records step"
    listed = [t for t in re.findall(r"`([^`]+)`", step.group(1)) if re.fullmatch(r"[a-z]+(-[a-z]+)*", t)]
    assert set(listed) == set(_slugs(agent_type)), listed


# 10. concurrency: not applicable (records are written once, in order, by one run) ----------------

# Extra error-path and CLI cases ------------------------------------------------------------------


def test_bad_inputs_are_rejected():
    ok = _answers()
    with pytest.raises(ValueError):
        adr.render_all("nonsense", "a", DATE, ok)
    with pytest.raises(ValueError):
        adr.render_all("hosted", "a", DATE, {k: v for k, v in ok.items() if k != "agent-type"})
    with pytest.raises(ValueError):
        adr.render_all("hosted", "a", DATE, {**ok, "agent-type": {"decision": "  "}})
    with pytest.raises(ValueError):
        adr.render_all("hosted", "a", DATE, {**ok, "agent-type": {"decision": "x", "chosen": "no-such-option"}})


def test_cli_writes_one_file_per_record(tmp_path):
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps(_answers("because")), encoding="utf-8")
    out = tmp_path / "docs" / "adr"
    proc = subprocess.run(
        [sys.executable, str(ADR_DIR / "render_adr.py"), "--type", "hosted", "--name", "demo-agent",
         "--date", DATE, "--answers", str(answers), "--out", str(out)],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    written = sorted(p.name for p in out.glob("*.md"))
    assert len(written) == 6, written
    assert written == sorted(_render("hosted", reason="because"))


# Readability defects found by reading real output ------------------------------------------------


def test_context_is_a_capitalised_question_without_author_notes():
    files = _render()
    assert len(files) == 6
    for name, text in files.items():
        ctx = re.search(r"## Context\n\n(.*?)\n\n## Decision", text, re.S).group(1)
        assert ctx[0].isupper() and ctx.endswith("?"), (name, ctx)
        assert "Grouped because" not in ctx and "Alone because" not in ctx, (name, ctx)
