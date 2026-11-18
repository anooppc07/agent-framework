"""Hosted shape: the real ResponsesHostServer driven in-process over ASGI (no port, no network, fake model).

Closes the handoff's open question: can ResponsesHostServer(workflow=factory, parse_response=...) host a
sequential chain and a group chat on the pinned beta?
"""
import httpx
import pytest

from fakes import Models
from agent_framework_foundry_hosting import ResponsesHostServer
from main import parse_response
from orchestration_flow import build_group_chat, build_sequential

ANSWERS = {"a": "A-SAYS", "b": "B-SAYS"}


def make_server(workflow_factory):
    """The same construction as main.build_server(), with a fake-model workflow factory."""
    return ResponsesHostServer(workflow=workflow_factory, parse_response=parse_response)


def sequential_factory(models):
    return lambda request: build_sequential([models.agent("a"), models.agent("b")])


def group_factory(models):
    return lambda request: build_group_chat([models.agent("a"), models.agent("b")],
                                            selection_func=lambda s: ["a", "b"][s.current_round % 2], max_rounds=2)


async def post(server, text, **extra):
    transport = httpx.ASGITransport(app=server)
    async with httpx.AsyncClient(transport=transport, base_url="http://host") as c:
        return await c.post("/responses", json={"input": text, "store": False, **extra}, timeout=30)


def output_text(resp):
    body = resp.json()
    return " ".join(part.get("text", "") for item in body.get("output", []) for part in item.get("content", []) or [])


# Case H1: a sequential workflow is hosted and the reviewer's reply is returned to the caller.
async def test_hosts_a_sequential_workflow():
    m = Models(answers=ANSWERS)
    resp = await post(make_server(sequential_factory(m)), "Write a tagline.")
    assert resp.status_code == 200, resp.text
    assert m.roles() == ["a", "b"]
    assert "B-SAYS" in output_text(resp)


# Case H2: a group chat is hosted the same way.
async def test_hosts_a_group_chat_workflow():
    m = Models(answers=ANSWERS)
    resp = await post(make_server(group_factory(m)), "Discuss.")
    assert resp.status_code == 200, resp.text
    assert m.roles() == ["a", "b"]
    text = output_text(resp)
    assert "A-SAYS" in text and "B-SAYS" in text, f"caller must receive the conversation, got: {text!r}"


# Case H3 (= case 8 at the host): blank input is rejected before any model call.
@pytest.mark.parametrize("bad", ["", "   "])
async def test_blank_request_is_rejected_before_any_model_call(bad):
    m = Models(answers=ANSWERS)
    resp = await post(make_server(sequential_factory(m)), bad)
    assert m.roles() == []
    body = resp.json()
    # A rejection must be recognisable: the host turns an exception into a generic server_error that a
    # caller cannot tell from an outage, so the template returns a normal, clearly marked result instead.
    assert body.get("status") == "completed", body
    assert output_text(resp).startswith("REJECTED:"), output_text(resp)


# Case H4: a fresh workflow is built for every request (the host forbids reusing a built graph).
async def test_factory_is_called_once_per_request():
    built = []
    m = Models(answers=ANSWERS)

    def factory(request):
        built.append(1)
        return build_sequential([m.agent("a"), m.agent("b")])

    server = make_server(factory)
    await post(server, "one")
    await post(server, "two")
    assert len(built) == 2


# Case H5: blank input to a group chat makes no model call and the caller gets a completed (not failed) run.
@pytest.mark.parametrize("bad", ["", "   "])
async def test_blank_request_to_group_chat_makes_no_model_call(bad):
    m = Models(answers=ANSWERS)
    resp = await post(make_server(group_factory(m)), bad)
    assert m.roles() == []
    assert resp.json().get("status") == "completed", resp.json()


# Case H6: a failure in a later agent, resumed from the last checkpoint, does not re-run earlier agents.
# Guards against wrapping the orchestration in an outer workflow, which re-runs every earlier agent.
@pytest.mark.parametrize("build", ["sequential", "group"])
async def test_resume_after_failure_does_not_rerun_earlier_agents(build):
    from agent_framework import InMemoryCheckpointStorage

    def make(models, storage):
        if build == "sequential":
            return build_sequential([models.agent("a"), models.agent("b"), models.agent("c")], checkpoint_storage=storage)
        return build_group_chat([models.agent("a"), models.agent("b"), models.agent("c")],
                                selection_func=lambda s: ["a", "b", "c"][s.current_round % 3], max_rounds=3,
                                checkpoint_storage=storage)

    storage = InMemoryCheckpointStorage()
    m1 = Models(answers={"a": "A", "b": "B", "c": "C"}, errors={"c": RuntimeError("down")})
    wf = make(m1, storage)
    with pytest.raises(RuntimeError):
        await wf.run("go", client_kwargs={"store": False})
    assert m1.roles() == ["a", "b", "c"]
    m2 = Models(answers={"a": "A", "b": "B", "c": "C"})
    await make(m2, storage).run(checkpoint_id=wf.get_last_checkpoint_id(), checkpoint_storage=storage,
                                client_kwargs={"store": False})
    assert m2.roles() == ["c"]
