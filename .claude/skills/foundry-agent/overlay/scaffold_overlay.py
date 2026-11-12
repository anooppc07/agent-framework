"""Mode-aware scaffold for the foundry-agent skill (plan Task 4).

    python scaffold_overlay.py --spec agent.json --out DIR --mode create-new|overlay-existing|design-only

* create-new       a complete MAF-hosted tool-less router in an absent or empty directory.
* overlay-existing only docs/runtime-contract.md and .json in an existing directory; nothing else changes.
* design-only      docs/runtime-design.md plus the contract; no executable, eval or pipeline files.

Everything is planned and validated before the first byte is written, and the write is all-or-nothing.
There is deliberately no overwrite, force or migrate option. Creation never deploys, installs a
workflow, or calls Azure. Exit codes: 0 success, 2 for a spec, mode, template or conflict error.
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import agent_spec as S
import runtime_contract as C

SKILL = Path(__file__).resolve().parent.parent
ROUTER_SRC = SKILL / "hosted" / "patterns" / "router-specialists"
HOSTED_SRC = SKILL / "hosted" / "assets"
DEFAULT_MODEL_VERSION = "2026-03-05"  # hosted/FLOW.md default; AgentSpec has no model-version field yet
DEFAULT_RESOURCES = {"cpu": "0.25", "memory": "0.5Gi"}
_PLACEHOLDER = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
_IGNORED = {"__pycache__", ".pytest_cache"}

# Router files shipped verbatim (placeholders filled) from the pinned, tested templates.
ROUTER_FILES = [(ROUTER_SRC / "agent.py", "agent.py"), (ROUTER_SRC / "main.py", "main.py"),
                (ROUTER_SRC / "requirements.txt", "requirements.txt"), (HOSTED_SRC / "preflight.py", "preflight.py")]
# Evaluation and release-pipeline generation are wired in below. The flag stays so the intake can offer the
# questions only while the renderers exist.
EXTRAS_AVAILABLE = True
# Development, governance and release files: present in the project, never in the deployed package.
PACKAGE_EXCLUDES = ["preflight.py", "run_routing_evaluation.py", "tests/", "docs/", "evals/", "pipeline/",
                    "agent-spec.json", "scaffold-metadata.json"]


# --- safety ------------------------------------------------------------------------------------


def contained_path(root, rel):
    """Resolve `rel` under `root`, refusing anything that would land outside it."""
    text = str(rel)
    if text.startswith(("/", "\\")) or _WINDOWS_DRIVE.match(text) or ".." in re.split(r"[\\/]", text):
        raise S.SpecError(f"output path {text!r} is outside the target directory")
    base = Path(root).resolve()
    target = (base / text).resolve()
    if base != target and base not in target.parents:
        raise S.SpecError(f"output path {text!r} is outside the target directory")
    return target


def _refuse_symlink(path):
    p = Path(path)
    for candidate in [p, *p.parents]:
        if candidate.is_symlink():
            raise S.SpecError(f"output path {path} involves a symlink ({candidate}); use a real directory")
        if candidate == candidate.parent:
            break


def _fill(text, values):
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _read_source(path):
    if not path.exists():
        raise S.SpecError(f"template source {path} is missing; the skill install is incomplete")
    return path.read_text(encoding="utf-8")


# --- all-or-nothing writing -----------------------------------------------------------------------


def write_plan(out_dir, plan):
    """Render every file (callables or text) first, then write them together. Returns written paths.

    `plan` maps a relative path to text or to a zero-argument callable returning text. All content is
    rendered and all paths are validated before anything touches the target; files are staged in a
    temporary sibling directory and moved into place, and a failure removes whatever was placed.
    """
    out = Path(out_dir)
    rendered = {}
    for rel, content in plan.items():
        text = content() if callable(content) else content
        if isinstance(text, bytes):  # copied verbatim from a pinned template; checked below as text
            check = text.decode("utf-8", errors="replace")
        else:
            check = text
        if _PLACEHOLDER.search(check) and not rel.startswith("evals/"):
            raise S.SpecError(f"{rel}: an unresolved author-time placeholder remains")
        rendered[contained_path(out, rel)] = text
    created_dirs, placed = [], []
    try:
        for target in rendered:
            for parent in reversed([p for p in target.parents if out.resolve() in p.parents or p == out.resolve()]):
                if not parent.exists():
                    parent.mkdir()
                    created_dirs.append(parent)
        for target, text in rendered.items():
            if target.exists():
                raise S.SpecError(f"{target} already exists; refusing to overwrite")
            if isinstance(text, bytes):
                target.write_bytes(text)
            else:
                target.write_text(text, encoding="utf-8", newline="\n")
            placed.append(target)
    except BaseException:
        for f in placed:
            f.unlink(missing_ok=True)
        for d in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        raise
    return list(rendered)


# --- planning -----------------------------------------------------------------------------------


def _inner(text):
    """Escape text for placement inside an existing double-quoted string literal."""
    return json.dumps(text, ensure_ascii=False)[1:-1]


def _python_values(spec):
    return {"AGENT_NAME": spec.name, "MODEL": _inner(spec.model)}


def _yaml_values(spec):
    resources = {**DEFAULT_RESOURCES, **spec.resource}
    return {
        "AGENT_NAME": spec.name,
        "AGENT_DESCRIPTION": S.yaml_scalar(spec.description),
        "MODEL": S.yaml_scalar(spec.model),
        "MODEL_VERSION": DEFAULT_MODEL_VERSION,
        "CPU": _inner(resources["cpu"]),
        "MEMORY": S.yaml_scalar(resources["memory"]),
    }


def _ignore_file():
    lines = _read_source(HOSTED_SRC / ".agentignore").splitlines()
    return "\n".join(lines + [e for e in PACKAGE_EXCLUDES if e not in lines]) + "\n"


def _specialists_json(spec):
    data = {
        "router_instructions": f"You route requests for {spec.name} to exactly one specialist. {spec.description}",
        "specialists": {s.name: s.instructions for s in spec.specialists},
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _metadata(spec, mode, selection="asserted-by-caller", recommendation=None):
    contract = C.build_contract(spec, mode)
    return json.dumps({
        "phase": "phase-1-local",
        "mode": mode,
        "pattern": spec.pattern,
        "agent_kind": spec.agent_kind,
        "framework": spec.framework,
        "entrypoint": "main.py",
        "template": {"name": spec.pattern, "source": "hosted/patterns/router-specialists"},
        "selection": {"agent_kind": spec.agent_kind, "pattern": spec.pattern, "framework": spec.framework,
                      "confirmation": selection},
        "rationale": spec.pattern_rationale,
        "recommendation": recommendation,
        "session_manager": {"required": contract.session.required, "declared": contract.session.declared,
                            "observed": contract.session.observed},
        "evaluation": contract.evaluation_status,
        "pipeline": contract.pipeline_status,
        "deployment": {"performed": False, "note": "Creation never deploys, installs a workflow or calls Azure."},
        "pending_phase_2": [
            "effective Foundry session-manager enablement and multi-turn continuity",
            "restart persistence and user/tenant isolation",
            "live evaluation, promotion evidence and (for routers) candidate-scoped route evidence",
        ],
    }, indent=2, ensure_ascii=False) + "\n"


def _contract_files(spec, mode):
    contract = C.build_contract(spec, mode)
    return {"docs/runtime-contract.md": C.render_markdown(contract),
            "docs/runtime-contract.json": C.render_json(contract) + "\n"}


def _design_text(spec):
    status = S.PATTERNS[spec.pattern]
    topology = ", ".join(s.name for s in spec.specialists) or "not defined"
    return "\n".join([
        f"# Runtime design proposal: {spec.name}", "",
        "> Design only. No code, evaluation or pipeline was generated, and nothing is implemented.", "",
        f"* Agent kind: {spec.agent_kind}", f"* Pattern: {spec.pattern} ({status.status})",
        f"* Summary: {status.summary}", f"* Rationale: {spec.pattern_rationale}",
        f"* Specialists: {topology}",
        f"* Evaluation selected: {'yes' if spec.evals_enabled else 'no'}; "
        f"pipeline selected: {'yes' if spec.pipeline_enabled else 'no'} (recorded, not generated)", "",
        "## Unresolved before this can run", "",
        "* Pattern semantics, control and termination contracts, and validation criteria.",
        "* Identity propagation, tool access and approval enforcement.",
        "* Session, memory and tenant-isolation decisions, with hosted evidence.",
        "* Release evidence: approved evaluation cases, thresholds and a promotion decision.", "",
        "See docs/runtime-contract.md for the control-by-control status.", ""])


ADR_SLUGS = ("agent-type", "model-runtime", "data-access", "behavior-boundaries", "evaluation", "container-sizing")
ADR_DIR = SKILL / "adr"


def _check_reasons(reasons):
    """Reasons are the user's own words keyed by ADR slug. Anything else is a caller error."""
    if reasons is None:
        return {}
    if not isinstance(reasons, dict):
        raise S.SpecError("reasons: must be a mapping of decision slug to the user's reason")
    unknown = sorted(set(reasons) - set(ADR_SLUGS))
    if unknown:
        raise S.SpecError(f"reasons: unknown decision(s) {', '.join(unknown)}; expected {', '.join(ADR_SLUGS)}")
    for slug, reason in reasons.items():
        if not isinstance(reason, str):
            raise S.SpecError(f"reasons: the reason for {slug} must be text")
    return {slug: reason.strip() for slug, reason in reasons.items() if reason.strip()}


def adr_answers(spec, reasons):
    """Decision-record answers derived only from the spec; reasons only from the user.

    The option labels must match adr/CATALOG.md exactly; the renderer rejects anything else.
    """
    reasons = _check_reasons(reasons)
    resources = {**DEFAULT_RESOURCES, **spec.resource}
    sizing_default = resources == DEFAULT_RESOURCES
    specialists = "; ".join(f"{s.name}: {s.instructions}" for s in spec.specialists) or "none defined"
    answers = {
        "agent-type": {
            "decision": f"A hosted agent ({spec.pattern}) running Microsoft Agent Framework in a Foundry container.",
            "chosen": "hosted"},
        "model-runtime": {
            "decision": f"The model deployment {spec.model} is named in azure.yaml.",
            "chosen": "Hosted: model named in `azure.yaml`"},
        "data-access": {
            "decision": "No tools: every specialist is tool-less, so the agent cannot call or change anything.",
            "chosen": "none"},
        "behavior-boundaries": {
            "decision": f"Each request goes to exactly one specialist or is refused. Specialist instructions: {specialists}",
            "chosen": "Hard scope limit"},
        "evaluation": {
            "decision": "Evaluation suite generated from the approved cases." if spec.evals_enabled
            else "No evaluation suite was generated.",
            "chosen": "Evals on (default)" if spec.evals_enabled else "Evals off"},
        "container-sizing": {
            "decision": f"{resources['cpu']} CPU and {resources['memory']} memory.",
            "chosen": "0.25 / 0.5Gi (default)" if sizing_default else "1 / 2Gi or 2 / 4Gi"},
    }
    for slug, reason in reasons.items():
        answers[slug]["reason"] = reason
    return answers


def _adr_files(spec, reasons):
    """Render the hosted decision records with the real renderer; returns {relative path: text}."""
    sys.path.insert(0, str(ADR_DIR))
    try:
        import render_adr
        try:
            rendered = render_adr.render_all("hosted", spec.name, __import__("datetime").date.today().isoformat(),
                                             adr_answers(spec, reasons), pipeline=False)
        except ValueError as exc:
            raise S.SpecError(f"decision records: {exc}") from exc
    finally:
        sys.path.remove(str(ADR_DIR))
    return {f"docs/adr/{name}": text for name, text in rendered.items()}


def _extra_files(spec):
    """Evaluation tree and staged pipeline, rendered in memory so they join the single write.

    Neither renderer touches the disk here. The pipeline is staged under pipeline/ inside the agent folder and
    is never placed in a .github/workflows directory.
    """
    import evaluation_assets
    import pipeline_assets
    files = {rel: data for rel, data in evaluation_assets.eval_files(spec).items()}
    for name, text in pipeline_assets.pipeline_files(spec).items():
        files[f"{pipeline_assets.OUTPUT_SUBDIR}/{name}"] = text
    return files


def _extras_not_available(spec):
    if EXTRAS_AVAILABLE:
        return
    wanted = [n for n, on in (("evaluation", spec.evals_enabled), ("pipeline", spec.pipeline_enabled)) if on]
    if wanted:
        raise S.SpecError(
            f"{' and '.join(wanted)} generation is not available in this build of the scaffold yet; "
            "set evals_enabled and pipeline_enabled to false, or use --mode design-only to record the selection")


def plan_create_new(spec, selection="asserted-by-caller", recommendation=None, reasons=None):
    """Map relative path -> text for a complete MAF-hosted router package."""
    py, yaml_values = _python_values(spec), _yaml_values(spec)
    plan = {rel: _fill(_read_source(src), py) for src, rel in ROUTER_FILES}
    plan["specialists.json"] = _specialists_json(spec)
    plan["azure.yaml"] = _fill(_read_source(HOSTED_SRC / "azure.yaml"), yaml_values)
    plan[".agentignore"] = _ignore_file()
    plan["agent-spec.json"] = json.dumps(_spec_data(spec), indent=2, ensure_ascii=False) + "\n"
    plan["scaffold-metadata.json"] = _metadata(spec, "create-new", selection, recommendation)
    plan.update(_contract_files(spec, "create-new"))
    plan.update(_adr_files(spec, reasons))
    plan.update(_extra_files(spec))
    return plan


def _spec_data(spec):
    import dataclasses
    return dataclasses.asdict(spec)


# --- entry points -----------------------------------------------------------------------------------


def scaffold(spec, out_dir, *, mode, selection="asserted-by-caller", recommendation=None, reasons=None):
    """Validate everything, then write all files or none. Returns the paths written."""
    S.validate_for_mode(spec, mode)  # raises for an unknown mode, design-only patterns, router rules
    if spec.pipeline_enabled and not spec.evals_enabled:
        raise S.SpecError("pipeline_enabled requires evals_enabled")
    out = Path(out_dir)
    if out.exists():
        _refuse_symlink(out)
    if mode == "create-new":
        _extras_not_available(spec)
        if out.exists():
            if not out.is_dir():
                raise S.SpecError(f"{out} exists and is not a directory")
            if any(out.iterdir()):
                raise S.SpecError(f"{out} is not empty; create-new needs an absent or empty directory")
        plan = plan_create_new(spec, selection, recommendation, reasons)
    elif mode == "overlay-existing":
        if not out.exists():
            raise S.SpecError(f"{out} does not exist; overlay-existing needs an existing project directory")
        if not out.is_dir():
            raise S.SpecError(f"{out} is not a directory")
        plan = _contract_files(spec, mode)
    else:  # design-only
        if out.exists() and not out.is_dir():
            raise S.SpecError(f"{out} exists and is not a directory")
        plan = {"docs/runtime-design.md": _design_text(spec), **_contract_files(spec, mode)}
    _preflight_targets(out, plan)
    return write_plan(out, plan)


def _preflight_targets(out, plan):
    for rel in plan:
        target = contained_path(out, rel)
        if target.exists():
            raise S.SpecError(f"{target} already exists; refusing to overwrite")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Create local agent files from a spec. Never deploys, installs a workflow or calls Azure.")
    p.add_argument("--spec", required=True, help="path to the agent spec JSON")
    p.add_argument("--out", required=True, help="target directory")
    p.add_argument("--mode", required=True, help="create-new | overlay-existing | design-only")
    args = p.parse_args(argv)
    try:
        spec = S.load_spec(args.spec)
        written = scaffold(spec, args.out, mode=args.mode)
    except (S.SpecError, C.ContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for path in written:
        print(path)
    print(f"\nWrote {len(written)} local file(s). Nothing was deployed, installed or dispatched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
