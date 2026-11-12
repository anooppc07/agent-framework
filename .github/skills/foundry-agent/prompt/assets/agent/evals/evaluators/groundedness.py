"""Groundedness evaluators: groundedness, similarity.

Drop this module (and the ``context``/``ground_truth`` dataset columns it reads) when
grounding=none. The ``context`` field in each dataset row records what the grounding tool returned
for that turn so ``groundedness`` can check the response against it; ``similarity`` compares the
response against the hand-written ``ground_truth``.
"""

from azure.ai.projects.models import TestingCriterionAzureAIEvaluator


def get_groundedness_evaluators(model_deployment: str) -> list[TestingCriterionAzureAIEvaluator]:
    """Return groundedness and similarity evaluators."""
    return [
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="groundedness",
            evaluator_name="builtin.groundedness",
            initialization_parameters={"model": model_deployment},
            data_mapping={
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}",
                "context": "{{item.context}}",
            },
        ),
        TestingCriterionAzureAIEvaluator(
            type="azure_ai_evaluator",
            name="similarity",
            evaluator_name="builtin.similarity",
            initialization_parameters={"model": model_deployment},
            data_mapping={
                "query": "{{item.query}}",
                "response": "{{sample.output_text}}",
                "ground_truth": "{{item.ground_truth}}",
            },
        ),
    ]
