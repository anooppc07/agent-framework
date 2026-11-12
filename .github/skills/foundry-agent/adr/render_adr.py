#!/usr/bin/env python3
"""Render decision records (ADRs) for a Foundry agent from CATALOG.md and TEMPLATE.md.

    python render_adr.py --type hosted --name my-agent --answers answers.json --out my-agent/docs/adr

answers.json maps each slug to {"decision": "...", "reason": "...", "chosen": "option label"}.
`reason` and `chosen` are optional; a blank reason renders as "Reason not recorded." (never invented).
Deploy-time records (agent-identity-role, existing-project-reuse) are not rendered here.
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TYPES = ("prompt", "hosted")
NO_REASON = "Reason not recorded."
_HEADING = re.compile(r"^#{2,3} (.+)$", re.M)
_SLUG_HEADING = re.compile(r"^([a-z]+(?:-[a-z]+)*) \((?:alone|together)")
_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")


def _clean(label):
    return re.sub(r"\s*\(.*\)$", "", label).casefold()


def _question(text):
    text = text.strip()
    return (text[:1].upper() + text[1:]).rstrip("?.") + "?" if text else text


def catalog():
    """Parse CATALOG.md into {slug: {...}} in file order."""
    text = (HERE / "CATALOG.md").read_text(encoding="utf-8")
    heads = list(_HEADING.finditer(text))
    out, deploy = {}, False
    for i, h in enumerate(heads):
        body = text[h.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        if h.group(1).startswith("Deployment decisions"):
            deploy = True
            continue
        m = _SLUG_HEADING.match(h.group(1))
        if not m:
            continue
        applies = re.search(r"Applies to: (.*?)\. Slug", body)
        related = re.search(r"Related: ((?:`[a-z-]+`(?:, )?)+)", body)
        question = re.search(r"^\* Question: (.*)$", body, re.M)
        why = re.search(r"^\* ((?:Grouped|Alone) because.*)$", body, re.M)
        options = [
            (r.group(1), r.group(2))
            for line in body.splitlines()
            if (r := _ROW.match(line)) and r.group(1) != "Option" and set(r.group(1)) - set("-: ")
        ]
        out[m.group(1)] = {
            "deploy_time": deploy,
            "types": {t for t in TYPES if applies and re.search(rf"\b{t}\b", applies.group(1))},
            "related": re.findall(r"`([a-z-]+)`", related.group(1)) if related else [],
            "question": question.group(1) if question else "",
            "why": why.group(1) if why else "",
            "options": options,
        }
    return out


def plan(agent_type, pipeline=True):
    """Ordered [(number, slug)] for the questionnaire records of this agent type."""
    slugs = [
        s for s, e in catalog().items()
        if not e["deploy_time"] and agent_type in e["types"] and (pipeline or s != "pipeline")
    ]
    return [(f"{i:04d}", s) for i, s in enumerate(slugs, start=1)]


def render_all(agent_type, agent_name, date, answers, pipeline=True):
    """Return {filename: markdown}. Raises ValueError on bad input."""
    if agent_type not in TYPES:
        raise ValueError(f"type must be one of {TYPES}, got {agent_type!r}")
    cat, steps = catalog(), plan(agent_type, pipeline)
    names = {s: f"{n}-{s}.md" for n, s in steps}
    template = (HERE / "TEMPLATE.md").read_text(encoding="utf-8")
    files = {}
    for number, slug in steps:
        a = answers.get(slug)
        if not a or not str(a.get("decision") or "").strip():
            raise ValueError(f"missing decision for {slug!r}")
        entry = cat[slug]
        chosen = a.get("chosen") or []
        chosen = [chosen] if isinstance(chosen, str) else chosen
        labels = {_clean(label) for label, _ in entry["options"]} | {l.casefold() for l, _ in entry["options"]}
        bad = [c for c in chosen if c.casefold() not in labels]
        if bad:
            raise ValueError(f"{slug}: unknown option {bad}; known: {[l for l, _ in entry['options']]}")
        picked = lambda label: _clean(label) in {c.casefold() for c in chosen} or label.casefold() in {c.casefold() for c in chosen}
        related = ([] if slug == "agent-type" else ["agent-type"]) + entry["related"]
        related = [names[r] for r in dict.fromkeys(related) if r in names]
        values = {
            "NNNN": number,
            "TITLE": slug.replace("-", " ").capitalize(),
            "DATE": date,
            "AGENT_NAME": agent_name,
            "RELATED": ", ".join(related) or "None",
            "CONTEXT": _question(entry["question"]),
            "DECISION": str(a["decision"]).strip(),
            "OPTIONS": "\n".join(f"- **{l}**{' (chosen)' if picked(l) else ''}: {c}" for l, c in entry["options"]),
            "REASON": str(a.get("reason") or "").strip() or NO_REASON,
            "CONSEQUENCES": "\n".join(f"- {c}" for l, c in entry["options"] if picked(l))
            or "Depends on the option chosen; see Options considered.",
        }
        files[names[slug]] = re.sub(r"\{\{([A-Z_]+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)
    return files


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--type", required=True, choices=TYPES)
    p.add_argument("--name", required=True)
    p.add_argument("--answers", required=True, help="JSON file: {slug: {decision, reason?, chosen?}}")
    p.add_argument("--out", required=True)
    p.add_argument("--date", default=datetime.date.today().isoformat())
    p.add_argument("--no-pipeline", action="store_true")
    args = p.parse_args(argv)
    try:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        files = render_all(args.type, args.name, args.date, answers, pipeline=not args.no_pipeline)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    print(f"wrote {len(files)} records to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
