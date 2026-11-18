"""Evaluator configuration modules for the poc-local-agent Foundry evaluation suite.

Each module returns a list of ``TestingCriterionAzureAIEvaluator`` built around Foundry's
``builtin.*`` evaluators. They are grouped by concern:

    quality.py       coherence, fluency, relevance
    safety.py        violence, sexual, self_harm, hate_unfairness
    task.py          task_adherence, intent_resolution
"""
