"""Safety evaluators: violence, sexual, self_harm, hate_unfairness.

Run on every case regardless of category — safety is a floor, not a scenario-specific check. Zero
violations tolerated on the baseline dataset.
"""

from azure.ai.projects.models import TestingCriterionAzureAIEvaluator

_SAFETY_NAMES = ("violence", "sexual", "self_harm", "hate_unfairness")


def get_safety_evaluators(
    model_deployment: str, for_traces: bool = False
) -> list[TestingCriterionAzureAIEvaluator]:
    """Return the four content-safety evaluators.

    See ``quality.get_quality_evaluators`` for what ``for_traces`` does.
    """
    criteria = [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name=name,
            evaluator_name=f"builtin.{name}",
            initialization_parameters={"model": model_deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_text}}"},
        )
        for name in _SAFETY_NAMES
    ]

    if for_traces:
        for criterion in criteria:
            criterion.pop("data_mapping", None)

    return criteria
