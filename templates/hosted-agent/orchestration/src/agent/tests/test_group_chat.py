"""Cases 4, 5, 6, 9, 10 (group chat). Fake model only; no mocks of our own modules."""
import pytest

from fakes import Models
from orchestration_flow import build_group_chat

RUN = {"client_kwargs": {"store": False}}
ANSWERS = {"alpha": "ALPHA-SAYS", "beta": "BETA-SAYS", "gamma": "GAMMA-SAYS"}


def round_robin(state):
    names = list(state.participants)
    return names[state.current_round % len(names)]


def count(seen_lists, text):
    """For each model call, how many times `text` was in what that model received."""
    return [sum(1 for t in seen if t == text) for seen in seen_lists]


# Case 4: round-robin over 3 participants stops at max_rounds and no participant sees a message twice.
async def test_round_robin_stops_and_never_duplicates():
    m = Models(answers=ANSWERS)
    agents = [m.agent(r) for r in ("alpha", "beta", "gamma")]
    wf = build_group_chat(agents, selection_func=round_robin, max_rounds=4)
    await wf.run("Discuss.", **RUN)
    assert m.roles() == ["alpha", "beta", "gamma", "alpha"]
    for role in ("beta", "gamma", "alpha"):
        for seen in m.texts_seen_by(role):
            assert len(seen) == len(set(seen)), f"{role} received a repeated message: {seen}"
    assert count(m.texts_seen_by("beta"), "ALPHA-SAYS") == [1]


# Case 5: the selector decides from the forwarded history, not from a private copy.
async def test_selector_sees_forwarded_history():
    m = Models(answers={"alpha": "ALPHA-SAYS", "beta": "BETA-SAYS"})
    seen_by_selector = []

    def pick(state):
        seen_by_selector.append([x.text for x in state.conversation])
        return "alpha" if state.current_round == 0 else "beta"

    wf = build_group_chat([m.agent("alpha"), m.agent("beta")], selection_func=pick, max_rounds=2)
    await wf.run("Discuss.", **RUN)
    assert m.roles() == ["alpha", "beta"]
    assert seen_by_selector[0] == ["Discuss."]
    assert "ALPHA-SAYS" in seen_by_selector[1]


# Case 6: a selector that never stops is bounded by max_rounds, not by the process.
async def test_runaway_selector_is_stopped_by_max_rounds():
    m = Models(answers=ANSWERS)
    wf = build_group_chat([m.agent("alpha"), m.agent("beta")], selection_func=lambda s: "alpha", max_rounds=3)
    await wf.run("Discuss.", **RUN)
    assert m.roles() == ["alpha", "alpha", "alpha"]


# Case 9: a single participant works.
async def test_single_participant_chat():
    m = Models(answers={"solo": "SOLO-SAYS"})
    wf = build_group_chat([m.agent("solo")], selection_func=lambda s: "solo", max_rounds=1)
    result = await wf.run("Discuss.", **RUN)
    assert m.roles() == ["solo"]
    assert result.get_outputs()


# Case 10: max_rounds=N allows exactly N speakers, not N-1 or N+1.
@pytest.mark.parametrize("n", [1, 2, 5])
async def test_max_rounds_boundary_is_exact(n):
    m = Models(answers=ANSWERS)
    wf = build_group_chat([m.agent("alpha"), m.agent("beta")], selection_func=round_robin, max_rounds=n)
    await wf.run("Discuss.", **RUN)
    assert len(m.roles()) == n


# Case 10b: max_rounds must be required and positive, since an unbounded chat is the failure case 6 guards.
@pytest.mark.parametrize("bad", [0, -1, None])
def test_non_positive_or_missing_max_rounds_rejected(bad):
    m = Models()
    with pytest.raises(ValueError):
        build_group_chat([m.agent("alpha")], selection_func=lambda s: "alpha", max_rounds=bad)
