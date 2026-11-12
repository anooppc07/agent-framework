"""Render the optional hosted release pipeline for a MAF agent (plan Task 7).

Renders agent-local files into `<agent>/pipeline/`: a PR validation workflow, a manual release workflow, a
standard-library lifecycle helper, the release configuration and a README. It only writes files:
it never installs a workflow into a `.github/workflows` directory, dispatches anything, or
contacts GitHub or Azure. Every source and output is checked before the first byte is written.

Sources live in `hosted/assets/pipeline/`. `release.json` is derived from the confirmed `AgentSpec`;
values the spec leaves unconfigured are recorded as blockers, never invented.
"""

import json
import re
from pathlib import Path

from agent_spec import PATTERNS, SpecError

PIPELINE_DIR = Path(__file__).resolve().parent.parent / "hosted" / "assets" / "pipeline"
OUTPUT_SUBDIR = "pipeline"
SOURCES = ("agent-ci.yml", "agent-deploy.yml", "hosted_pipeline.py", "README.md")
GENERATED = ("release.json",)
DATASET_PATH = "evals/evals.yaml"  # the canonical release dataset the evaluation assets write
DEFAULT_ADAPTER = "unconfigured"

_PLACEHOLDER = re.compile(r"(?<!\$)\{\{([A-Z][A-Z0-9_]*)\}\}")
_SEGMENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class PipelineError(SpecError):
    """The pipeline cannot be generated for this spec, or its sources/outputs are unusable."""


def _agent_dir(spec, agent_dir):
    """A relative POSIX folder the workflows can use in `paths:` and `working-directory:`."""
    value = f"src/agents/{spec.name}" if agent_dir is None else agent_dir
    if not isinstance(value, str) or not value or value.startswith(("/", "-")) or "\\" in value:
        raise PipelineError(f"agent_dir {value!r} must be a relative POSIX folder")
    parts = value.split("/")
    if value != "." and (any(p in ("", ".", "..") for p in parts) or not all(_SEGMENT.match(p) for p in parts)):
        raise PipelineError(f"agent_dir {value!r} has an unsafe path segment")
    return value


def _check_spec(spec):
    if not spec.pipeline_enabled:
        return False
    if not spec.evals_enabled:
        raise PipelineError("the release pipeline requires evals_enabled")
    if spec.agent_kind != "hosted" or spec.framework != "maf":
        raise PipelineError("the hosted release pipeline is for MAF-hosted agents only")
    if PATTERNS[spec.pattern].status != "runnable-unvalidated":
        raise PipelineError(f"pattern {spec.pattern!r} is design-only and has no pipeline")
    return True


def _blockers(spec):
    """Configuration the owner has not confirmed. The generator reports it; it never fills it in."""
    cfg, found = spec.evaluation, []
    if not spec.evaluation_configured:
        found.append("evaluation is unconfigured: judge model, evaluators, threshold and approved cases are required")
    if not cfg.judge_model:
        found.append("evaluation.judge_model is unconfigured")
    if cfg.min_pass_rate is None:
        found.append("evaluation.min_pass_rate is unconfigured")
    if not cfg.evaluators:
        found.append("evaluation.evaluators is unconfigured")
    if cfg.dataset_status != "approved":
        found.append("evaluation dataset is not approved")
    if spec.hosted_session_manager != "enabled":
        found.append(f"hosted session manager is {spec.hosted_session_manager!r}; it must be confirmed enabled")
    return found


def _release_config(spec):
    cfg = spec.evaluation
    return {
        "schema": 1, "agent": spec.name, "pattern": spec.pattern, "router": spec.pattern == "router-specialists",
        "adapter": DEFAULT_ADAPTER, "dataset_path": DATASET_PATH, "judge_model": cfg.judge_model,
        "evaluators": list(cfg.evaluators), "min_pass_rate": cfg.min_pass_rate, "case_count": len(cfg.cases),
        "dataset_status": cfg.dataset_status,
        "tools": [{"name": t.name, "auth": t.auth, "requires_approval": t.requires_approval} for t in spec.tools],
        "blockers": _blockers(spec),
    }


def _read_sources(source_dir):
    folder = Path(source_dir)
    texts, problems = {}, []
    for name in SOURCES:
        try:
            texts[name] = (folder / name).read_text(encoding="utf-8")
        except OSError as exc:
            problems.append(f"{folder / name}: {exc.strerror or exc}")
    if problems:
        raise PipelineError("pipeline source files are unusable: " + "; ".join(problems))
    return texts


def _fill(name, text, values):
    def sub(match):
        key = match.group(1)
        if key not in values:
            raise PipelineError(f"{name}: unknown placeholder {{{{{key}}}}}")
        return values[key]

    return _PLACEHOLDER.sub(sub, text)


def pipeline_files(spec, *, agent_dir=None, source_dir=None):
    """Rendered file name -> text. Empty when the pipeline is not selected. Touches no output."""
    if not _check_spec(spec):
        return {}
    directory = _agent_dir(spec, agent_dir)
    values = {"AGENT_NAME": spec.name, "AGENT_DIR": directory}
    files = {name: _fill(name, text, values)
             for name, text in _read_sources(source_dir or PIPELINE_DIR).items()}
    files["release.json"] = json.dumps(_release_config(spec), indent=2, ensure_ascii=False) + "\n"
    return files


def _reject_workflows_dir(target):
    if any(part.lower() == "workflows" for part in target.parts) and ".github" in (p.lower() for p in target.parts):
        raise PipelineError(f"{target}: refusing to write into a .github/workflows directory; "
                            "workflows are installed only by the deploy skill")


def render_pipeline(spec, out_dir, *, agent_dir=None, source_dir=None):
    """Write the selected pipeline files under `<out_dir>/pipeline/`; returns the written paths."""
    files = pipeline_files(spec, agent_dir=agent_dir, source_dir=source_dir)
    if not files:
        return []
    target = Path(out_dir) / OUTPUT_SUBDIR
    _reject_workflows_dir(target)
    clashes = [str(target / name) for name in files if (target / name).exists()]
    if clashes:
        raise PipelineError("refusing to overwrite existing files: " + ", ".join(clashes))
    if target.exists() and not target.is_dir():
        raise PipelineError(f"{target} exists and is not a directory")
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in files.items():
        path = target / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written
