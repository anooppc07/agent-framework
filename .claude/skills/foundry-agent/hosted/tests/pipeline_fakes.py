"""Shared fixtures for the hosted-pipeline tests (plan Task 7).

The only fake is `FakeAdapter`, a hand-written stand-in for the external Foundry/CLI boundary
(the `LifecycleAdapter` interface). Identities, gate, digests, config loading and the CLI under test
are the real code, working on real files.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # do not litter the source tree with bytecode

PIPELINE_SRC = Path(__file__).resolve().parent.parent / "assets" / "pipeline"
sys.path.insert(0, str(PIPELINE_SRC))

import hosted_pipeline as hp  # noqa: E402

EVALUATORS = ("relevance", "task_adherence")


def identity(**over):
    values = dict(
        agent="card-helper", environment="dev", run_id="1001-1", source_id="a" * 40,
        artifact_id="sha256:art", dataset_hash="sha256:data", evaluators=EVALUATORS,
        min_pass_rate=0.8, case_count=10, criteria_hash="sha256:crit", router=False, blockers=(),
    )
    values.update(over)
    return hp.ReleaseIdentity(**values)


def good_route(ident, cand, **over):
    values = dict(candidate_name=cand.name, version=cand.version, scope="candidate",
                  cases_total=ident.case_count, cases_correct=ident.case_count)
    values.update(over)
    return hp.RouteEvidence(**values)


def good_eval(ident, cand, **over):
    values = dict(
        candidate_name=cand.name, version=cand.version, dataset_hash=ident.dataset_hash,
        evaluator_set=tuple(ident.evaluators),
        pass_counts={e: (ident.case_count, ident.case_count) for e in ident.evaluators},
        status="completed", artifact_id=ident.artifact_id, source_id=ident.source_id,
        run_id=ident.run_id, criteria_hash=ident.criteria_hash,
        route_evidence=good_route(ident, cand) if ident.router else None,
    )
    values.update(over)
    return hp.EvaluationResult(**values)


def good_session(cand, **over):
    values = dict(candidate_name=cand.name, version=cand.version, manager_enabled=True,
                  continuity_passed=True)
    values.update(over)
    return hp.SessionEvidence(**values)


class FakeAdapter(hp.LifecycleAdapter):
    """Models a hosted platform with one serving version. Every call is logged in order.

    Options are plain attributes so a test states exactly which boundary misbehaves.
    """

    name = "fake"

    def __init__(self, *, serving="2", new_version="3", caps=None, artifact=None):
        self.serving = serving
        self.new_version = new_version
        self.caps = caps if caps is not None else hp.Capabilities(True, True, True, "fake")
        self.artifact = artifact  # None: report the artifact identity that was asked for
        self.log = []
        self.promoted = []
        self.rolled_back = []
        # misbehaviour switches
        self.deploy_makes_serving = False
        self.deploy_error = None
        self.resolved_version = None
        self.eval_override = None      # callable(ident, cand) -> EvaluationResult | None
        self.eval_error = None
        self.session_override = None   # callable(cand) -> SessionEvidence | None
        self.session_error = None
        self.promote_error = None
        self.promote_noop = False
        self.promote_moves_then_raises = False
        self.smoke_override = None     # callable(cand) -> SmokeResult
        self.smoke_error = None
        self.rollback_error = None
        self.rollback_noop = False
        self.serving_error = None
        self.caps_error = None
        self._ident = None

    def called(self, op):
        return [e for e in self.log if e == op]

    def capabilities(self):
        self.log.append("capabilities")
        if self.caps_error:
            raise self.caps_error
        return self.caps

    def serving_version(self, agent):
        self.log.append("serving_version")
        if self.serving_error:
            raise self.serving_error
        return self.serving

    def deploy_candidate(self, ident):
        self.log.append("deploy_candidate")
        self._ident = ident
        if self.deploy_error:
            raise self.deploy_error
        if self.deploy_makes_serving:
            self.serving = self.new_version
        return hp.Candidate(ident.agent, self.new_version,
                            self.artifact if self.artifact is not None else ident.artifact_id)

    def resolve_candidate_version(self, cand):
        self.log.append("resolve_candidate_version")
        return self.resolved_version if self.resolved_version is not None else cand.version

    def run_evaluation(self, cand, ident):
        self.log.append("run_evaluation")
        if self.eval_error:
            raise self.eval_error
        if self.eval_override:
            return self.eval_override(ident, cand)
        return good_eval(ident, cand)

    def check_session(self, cand):
        self.log.append("check_session")
        if self.session_error:
            raise self.session_error
        if self.session_override:
            return self.session_override(cand)
        return good_session(cand)

    def promote(self, cand):
        self.log.append("promote")
        self.promoted.append(cand)
        if self.promote_error:
            if self.promote_moves_then_raises:
                self.serving = cand.version
            raise self.promote_error
        if not self.promote_noop:
            self.serving = cand.version

    def smoke(self, cand):
        self.log.append("smoke")
        if self.smoke_error:
            raise self.smoke_error
        if self.smoke_override:
            return self.smoke_override(cand)
        return hp.SmokeResult(True, 2, self.serving)

    def rollback(self, agent, to_version):
        self.log.append("rollback")
        self.rolled_back.append((agent, to_version))
        if self.rollback_error:
            raise self.rollback_error
        if not self.rollback_noop:
            self.serving = to_version


def make_agent_root(root, *, dataset="query-1\nquery-2\nquery-3\n", config=None):
    """A real on-disk agent project: deployable source, evals dataset and a pipeline folder."""
    import json

    root = Path(root)
    (root / "evals").mkdir(parents=True, exist_ok=True)
    (root / "pipeline").mkdir(exist_ok=True)
    (root / "agent.py").write_text("def build_agent():\n    return 'agent'\n", encoding="utf-8")
    (root / "main.py").write_text("print('main')\n", encoding="utf-8")
    (root / "requirements.txt").write_text("agent-framework-foundry==1.16.1\n", encoding="utf-8")
    (root / "instructions.md").write_text("You are a helpful card-dispute assistant.\n", encoding="utf-8")
    (root / "evals" / "evals.yaml").write_text(dataset, encoding="utf-8")
    for name in ("agent-ci.yml", "agent-deploy.yml", "hosted_pipeline.py"):
        (root / "pipeline" / name).write_text(f"# {name}\n", encoding="utf-8")
    cfg = {
        "schema": 1, "agent": "card-helper", "pattern": "single-agent", "router": False,
        "adapter": "fake", "dataset_path": "evals/evals.yaml", "judge_model": "judge-deployment",
        "evaluators": list(EVALUATORS), "min_pass_rate": 0.8, "case_count": 3,
        "dataset_status": "approved", "tools": [{"name": "lookup", "auth": "none", "requires_approval": False}],
        "blockers": [],
    }
    cfg.update(config or {})
    (root / "pipeline" / "release.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return root


def good_env(**over):
    env = {
        "GITHUB_SHA": "b" * 40, "GITHUB_RUN_ID": "777", "GITHUB_RUN_ATTEMPT": "2",
        "AZURE_CLIENT_ID": "x", "AZURE_TENANT_ID": "x", "AZURE_SUBSCRIPTION_ID": "x",
        "FOUNDRY_PROJECT_ENDPOINT": "x", "AZURE_AI_MODEL_DEPLOYMENT_NAME": "x",
    }
    env.update(over)
    return env
