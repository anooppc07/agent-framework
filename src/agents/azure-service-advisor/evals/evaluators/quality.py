"""Response quality evaluators: coherence, fluency, relevance.

Assess the agent's answers independent of grounding — whether the response is well-formed,
readable, and actually addresses what was asked.
"""

from azure.ai.projects.models import TestingCriterionAzureAIEvaluator


def get_quality_evaluators(
    model_deployment: str, for_traces: bool = False
) -> list[TestingCriterionAzureAIEvaluator]:
    """Return coherence, fluency, and relevance evaluators.

    When ``for_traces`` is True, ``data_mapping`` is omitted: trace-based evaluation
    (``azure_ai_source`` / scenario ``traces``) auto-populates query/response from the trace's
    ``gen_ai.input.messages`` / ``gen_ai.output.messages`` attributes, so a dataset-shaped mapping
    would be meaningless.
    """
    criteria = [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="coherence",
            evaluator_name="builtin.coherence",
            initialization_parameters={"model": model_deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_text}}"},
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="fluency",
            evaluator_name="builtin.fluency",
            initialization_parameters={"model": model_deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_text}}"},
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="relevance",
            evaluator_name="builtin.relevance",
            initialization_parameters={"model": model_deployment},
            data_mapping={"query": "{{item.query}}", "response": "{{sample.output_text}}"},
        ),
    ]

    if for_traces:
        for criterion in criteria:
            criterion.pop("data_mapping", None)

    return criteria
