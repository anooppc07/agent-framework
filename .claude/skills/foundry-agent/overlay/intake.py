"""Preset-driven intake for the foundry-agent skill.

A preset (for example examples/card-dispute/preset.json) pre-fills every answer. Each choice question
lists the preset value first, marked (Recommended), so the user can accept it or pick another option;
the owner is free text and never assumed. The skill's flow asks the questions, writes the answers to
a JSON file, then calls this module:

    python intake.py kind --preset P                          print Q0, prompt or hosted (JSON); ask it first
    python intake.py questions --preset P                     print the question batches (JSON)
    python intake.py build --preset P --answers A --out SPEC  merge preset + answers into a spec
    python intake.py create --spec SPEC --out DIR --confirm-pattern X --confirm-kind Y

A recommendation, a default or silence is never consent: create writes nothing unless the caller
passes a confirmation that matches the spec exactly. Creation is local only; it never deploys.
Exit codes: 0 success, 2 for a validation or confirmation problem.
"""

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import agent_spec as S
import scaffold_overlay as SC

RECOMMENDED = " (Recommended)"
MAX_OPTIONS = 4
MAX_PER_BATCH = 4
ORDER = ("pattern", "name", "owner", "classification", "model", "evals", "pipeline")
KINDS = ("hosted", "prompt")
ANSWER_KEYS = set(ORDER) | {"agent_kind"}
BASE_FLOW_PATTERN = "single-agent"
SINGLE_AGENT_DROPS = ("specialists", "out_of_scope_examples")


@dataclass(frozen=True)
class DispatchResult:
    action: str  # none | revalidate | created | delegate-base-flow | design-only
    written: tuple = ()
    reason: str = ""


# --- preset -------------------------------------------------------------------------------------


def load_preset(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise S.SpecError(f"{path}: cannot read preset ({exc.strerror or exc})") from exc
    except json.JSONDecodeError as exc:
        raise S.SpecError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(data, dict) or not isinstance(data.get("defaults"), dict) or not data["defaults"]:
        raise S.SpecError(f"{path}: preset needs a non-empty 'defaults' mapping")
    if not isinstance(data.get("questions"), dict):
        raise S.SpecError(f"{path}: preset needs a 'questions' mapping")
    return data


# --- questions ----------------------------------------------------------------------------------


def _default_for(preset, qid):
    d = preset["defaults"]
    if qid == "agent_kind":
        return d.get("agent_kind", "hosted")
    if qid == "evals":
        return "yes" if d.get("evals_enabled") else "no"
    if qid == "pipeline":
        return "yes" if d.get("pipeline_enabled") else "no"
    return d.get(qid)


def _offered(spec_q):
    needed = spec_q.get("requires")
    return needed is None or SC.EXTRAS_AVAILABLE


def _choice(preset, qid, q):
    default = _default_for(preset, qid)
    options = [dict(o) for o in q["options"]]
    options.sort(key=lambda o: o["value"] != default)  # stable: the preset value first
    if options and options[0]["value"] == default:
        options[0]["label"] = options[0]["label"].removesuffix(RECOMMENDED) + RECOMMENDED
    options = options[:MAX_OPTIONS]
    for o in options[1:]:
        o["label"] = o["label"].removesuffix(RECOMMENDED)
    return {"id": qid, "header": q["header"], "question": q["question"], "kind": "choice", "options": options,
            "allow_other": bool(q.get("open"))}


def kind_question(preset):
    """Q0: prompt or hosted, asked before anything else. The preset's own kind is listed first and recommended."""
    default = preset["defaults"].get("agent_kind", "hosted")
    q = _choice(preset, "agent_kind", preset["questions"]["agent_kind"])
    q["why"] = ("The preset builds a router with specialists, which needs code, so hosted is recommended."
                if default == "hosted" else "The preset is a declarative prompt agent.")
    return q


def questions(preset):
    """Question batches (at most four per batch) in a fixed order, ready for a selection prompt."""
    out = []
    for qid in ORDER:
        q = preset["questions"].get(qid)
        if q is None or not _offered(q):
            continue
        if q["kind"] == "text":
            out.append({"id": qid, "header": q["header"], "question": q["question"], "kind": "text"})
        else:
            out.append(_choice(preset, qid, q))
    return [out[i:i + MAX_PER_BATCH] for i in range(0, len(out), MAX_PER_BATCH)]


# --- build the spec -------------------------------------------------------------------------------


def _allowed(preset, qid):
    q = preset["questions"].get(qid, {})
    return {o["value"] for o in q.get("options", [])}, bool(q.get("open"))


def build_spec(preset, answers):
    """Merge preset defaults with the user's answers. Strict: unknown keys and bad choices are errors."""
    if not isinstance(answers, dict):
        raise S.SpecError("answers: must be a mapping")
    unknown = sorted(set(answers) - ANSWER_KEYS)
    if unknown:
        raise S.SpecError(f"answers: unknown key(s) {', '.join(unknown)}; expected agent_kind, {', '.join(ORDER)}")
    if "agent_kind" in answers:
        kind = answers["agent_kind"]
        if not isinstance(kind, str) or kind not in KINDS:
            raise S.SpecError(f"agent_kind: {kind!r} is not one of {', '.join(KINDS)}")
        if kind == "prompt":
            raise S.SpecError(
                "agent_kind: this preset builds a hosted router, which needs code; a prompt agent has no code and "
                "no router. Use the prompt flow (prompt/FLOW.md) for a declarative agent, or choose hosted. "
                "Nothing was written and no other type was substituted.")
    owner = answers.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        raise S.SpecError("owner: required; the owning team must be named (no default is assumed)")
    spec = copy.deepcopy(preset["defaults"])
    spec["owner"] = owner.strip()
    for qid in ("pattern", "name", "classification", "model"):
        if qid not in answers:
            continue
        value = answers[qid]
        values, open_ = _allowed(preset, qid)
        if not isinstance(value, str) or not value.strip():
            raise S.SpecError(f"{qid}: must be a non-blank string")
        if value not in values and not open_:
            raise S.SpecError(f"{qid}: {value!r} is not one of {', '.join(sorted(values))}")
        spec[qid] = value
    for qid, key in (("evals", "evals_enabled"), ("pipeline", "pipeline_enabled")):
        if qid not in answers:
            continue
        if answers[qid] not in ("yes", "no"):
            raise S.SpecError(f"{qid}: must be 'yes' or 'no'")
        if answers[qid] == "yes" and not SC.EXTRAS_AVAILABLE:
            raise S.SpecError(f"{qid}: generation is not available in this build of the scaffold yet; answer 'no'")
        spec[key] = answers[qid] == "yes"
    if spec.get("pipeline_enabled") and not spec.get("evals_enabled"):
        raise S.SpecError("pipeline: requires evals; answer evals 'yes' first")
    if spec["pattern"] == BASE_FLOW_PATTERN:
        for key in SINGLE_AGENT_DROPS:
            spec.pop(key, None)
    S.parse_spec(spec)  # fail here, with the spec's own message, rather than later at scaffold time
    return spec


# --- dispatch ---------------------------------------------------------------------------------------


def _confirmed(confirmation):
    return isinstance(confirmation, dict) and confirmation.get("confirmed") is True


def dispatch(spec_data, confirmation, out_dir, recommendation=None, reasons=None):
    """Create files only for an explicit confirmation that matches the spec. Otherwise change nothing."""
    if not _confirmed(confirmation):
        return DispatchResult("none", (), "no explicit confirmation from the user; nothing was written")
    spec = S.parse_spec(spec_data)
    asked = (confirmation.get("agent_kind"), confirmation.get("pattern"))
    if asked != (spec.agent_kind, spec.pattern):
        return DispatchResult(
            "revalidate", (),
            f"confirmed {asked[0]}/{asked[1]} but the spec is {spec.agent_kind}/{spec.pattern}; "
            "re-run the questions and confirm again")
    if S.PATTERNS[spec.pattern].status == "design-only":
        return DispatchResult("design-only", tuple(SC.scaffold(spec, out_dir, mode="design-only")),
                              "design proposal written; nothing is runnable")
    if spec.pattern == BASE_FLOW_PATTERN:
        return DispatchResult("delegate-base-flow", (),
                              "single-agent is created by the base hosted flow, not the router scaffold")
    written = SC.scaffold(spec, out_dir, mode="create-new", selection="user-confirmed",
                          recommendation=recommendation, reasons=reasons)
    return DispatchResult("created", tuple(written), "created locally; nothing was deployed")


# --- CLI --------------------------------------------------------------------------------------------


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    k = sub.add_parser("kind", help="print Q0 (prompt or hosted) as JSON; ask it before the other questions")
    k.add_argument("--preset", required=True)
    q = sub.add_parser("questions")
    q.add_argument("--preset", required=True)
    b = sub.add_parser("build")
    b.add_argument("--preset", required=True)
    b.add_argument("--answers", required=True)
    b.add_argument("--out", required=True)
    c = sub.add_parser("create")
    c.add_argument("--spec", required=True)
    c.add_argument("--out", required=True)
    c.add_argument("--confirm-pattern")
    c.add_argument("--confirm-kind")
    c.add_argument("--reasons", help="JSON file: {decision slug: the user's reason}; omitted reasons are recorded as not given")
    c.add_argument("--recommend-pattern", help="the pattern the skill recommended (recorded apart from the choice)")
    c.add_argument("--recommend-why", help="the skill's one-line reason for its recommendation")
    args = p.parse_args(argv)
    try:
        if args.command == "kind":
            print(json.dumps(kind_question(load_preset(args.preset)), indent=2, ensure_ascii=False))
            return 0
        if args.command == "questions":
            print(json.dumps(questions(load_preset(args.preset)), indent=2, ensure_ascii=False))
            return 0
        if args.command == "build":
            try:
                answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise S.SpecError(f"{args.answers}: cannot read answers ({exc})") from exc
            spec = build_spec(load_preset(args.preset), answers)
            Path(args.out).write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"spec written to {args.out}")
            return 0
        spec_data = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        confirmation = {"confirmed": bool(args.confirm_pattern and args.confirm_kind),
                        "pattern": args.confirm_pattern, "agent_kind": args.confirm_kind}
        recommendation = None
        if args.recommend_why and not args.recommend_pattern:
            raise S.SpecError("--recommend-why needs --recommend-pattern: a recommendation names a pattern")
        if args.recommend_pattern:
            recommendation = {"pattern": args.recommend_pattern}
            if args.recommend_why:
                recommendation["why"] = args.recommend_why
        reasons = None
        if args.reasons:
            try:
                reasons = json.loads(Path(args.reasons).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise S.SpecError(f"{args.reasons}: cannot read reasons ({exc})") from exc
        result = dispatch(spec_data, confirmation, args.out, recommendation=recommendation, reasons=reasons)
        if result.action in ("none", "revalidate"):
            print(f"error: {result.reason}. Pass --confirm-kind and --confirm-pattern once the user has chosen.",
                  file=sys.stderr)
            return 2
        for path in result.written:
            print(path)
        print("\n" + result.reason.removesuffix("; nothing was deployed") + ". "
              "Nothing was deployed, installed or dispatched.")
        return 0
    except (S.SpecError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
