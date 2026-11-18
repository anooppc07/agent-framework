"""Cases 1-3 (sequential) and 7-8 (empty/null input). Fake model only; no mocks of our own modules."""
import pytest
from agent_framework import AgentExecutor

from fakes import Models
from orchestration_flow import build_sequential, validate_input

RUN = {"client_kwargs": {"store": False}}


def agent_executors(wf):
    return {k: e for k, e in wf.executors.items() if isinstance(e, AgentExecutor)}


# Case 1: the reviewer's model receives the writer's reply exactly once.
async def test_reviewer_receives_writer_reply_once():
    m = Models(answers={"writer": "TAGLINE-X", "reviewer": "REVIEW-OF-X"})
    wf = build_sequential([m.agent("writer"), m.agent("reviewer")])
    await wf.run("Write a tagline.", **RUN)
    (seen,) = m.texts_seen_by("reviewer")
    assert seen == ["Write a tagline.", "TAGLINE-X"]


# Case 2: each executor owns a distinct AgentSession, and the reviewer's own history is not the writer's.
async def test_each_executor_has_its_own_session():
    m = Models(answers={"writer": "TAGLINE-X", "reviewer": "REVIEW-OF-X"})
    wf = build_sequential([m.agent("writer"), m.agent("reviewer")])
    await wf.run("Write a tagline.", **RUN)
    ex = agent_executors(wf)
    assert ex["writer"]._session is not ex["reviewer"]._session
    assert ex["writer"]._session.session_id != ex["reviewer"]._session.session_id
    writer_texts = [x.text for x in ex["writer"]._session.state["in_memory"]["messages"]]
    assert "REVIEW-OF-X" not in writer_texts


# Case 3: message order across three agents is deterministic and identical on every run.
async def test_order_is_deterministic_across_runs():
    orders = []
    for _ in range(3):
        m = Models(answers={"a": "A", "b": "B", "c": "C"})
        wf = build_sequential([m.agent("a"), m.agent("b"), m.agent("c")])
        await wf.run("go", **RUN)
        orders.append((m.roles(), m.texts_seen_by("c")))
    assert orders[0] == (["a", "b", "c"], [["go", "A", "B"]])
    assert orders[0] == orders[1] == orders[2]


# Case 7: None is rejected with ValueError.
def test_none_input_rejected():
    with pytest.raises(ValueError):
        validate_input(None)


# Case 8: empty and whitespace-only input are rejected, a normal string is returned stripped.
@pytest.mark.parametrize("bad", ["", " ", "\n\t  "])
def test_blank_input_rejected(bad):
    with pytest.raises(ValueError):
        validate_input(bad)


def test_valid_input_is_stripped():
    assert validate_input("  hello  ") == "hello"
