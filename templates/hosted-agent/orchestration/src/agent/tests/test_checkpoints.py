"""Cases 11-16: checkpoints, error path, concurrency, tenancy. Fake model only; no mocks of our own modules."""
import asyncio

import pytest
from agent_framework import AgentExecutor, InMemoryCheckpointStorage

from fakes import Models
from orchestration_flow import build_sequential
from tenant_store import TenantCheckpointStorage

RUN = {"client_kwargs": {"store": False}}
NAME = "sequential-v1"


def executors(wf):
    return {k: e for k, e in wf.executors.items() if isinstance(e, AgentExecutor)}


def fresh(models, storage, roles=("a", "b")):
    return build_sequential([models.agent(r) for r in roles], checkpoint_storage=storage)


# Case 11: a checkpoint taken before anything ran (zero messages) restores cleanly.
async def test_zero_message_checkpoint_restores():
    m = Models()
    wf = fresh(m, None)
    state = await executors(wf)["a"].on_checkpoint_save()
    wf2 = fresh(Models(), None)
    a2 = executors(wf2)["a"]
    await a2.on_checkpoint_restore(state)
    assert a2._session.state.get("in_memory", {}).get("messages", []) == []


# Case 12: a checkpoint with no agent_session restores to a fresh, empty session and the workflow still runs.
async def test_missing_agent_session_gives_fresh_session_and_workflow_completes():
    m = Models(answers={"a": "A", "b": "B"})
    wf = fresh(m, None)
    await wf.run("go", **RUN)
    state = await executors(wf)["a"].on_checkpoint_save()
    state.pop("agent_session")
    m2 = Models(answers={"a": "A", "b": "B"})
    wf2 = fresh(m2, None)
    a2 = executors(wf2)["a"]
    old_id = a2._session.session_id
    await a2.on_checkpoint_restore(state)
    assert a2._session.session_id != old_id
    assert a2._session.state.get("in_memory", {}).get("messages", []) == []
    result = await wf2.run("again", **RUN)
    assert result.get_outputs()


# Case 13: the second agent's model raises mid-run. The error surfaces, the first agent's work is not
# redone on resume, and the resumed run completes by calling only the failed agent.
async def test_failure_surfaces_and_resume_calls_only_failed_agent():
    storage = InMemoryCheckpointStorage()
    m = Models(answers={"a": "A", "b": "B"}, errors={"b": RuntimeError("model down")})
    wf = fresh(m, storage)
    with pytest.raises(RuntimeError, match="model down"):
        await wf.run("go", **RUN)
    assert m.roles() == ["a", "b"]
    last = wf.get_last_checkpoint_id()
    assert last is not None

    m2 = Models(answers={"a": "A", "b": "B"})
    wf2 = fresh(m2, storage)
    result = await wf2.run(checkpoint_id=last, checkpoint_storage=storage, **RUN)
    assert m2.roles() == ["b"]
    assert result.get_outputs()


# Case 14: two interleaved requests, each with its own factory-built workflow, sharing one storage.
async def test_interleaved_requests_do_not_leak():
    storage = InMemoryCheckpointStorage()
    ma = Models(answers={"a": "A-FOR-ONE", "b": "B-FOR-ONE"}, delays={"a": 0.05, "b": 0.05})
    mb = Models(answers={"a": "A-FOR-TWO", "b": "B-FOR-TWO"}, delays={"a": 0.01, "b": 0.01})
    wa, wb = fresh(ma, storage), fresh(mb, storage)
    await asyncio.gather(wa.run("PROMPT-ONE", **RUN), wb.run("PROMPT-TWO", **RUN))
    flat_a = [t for lst in ma.texts_seen_by("a") + ma.texts_seen_by("b") for t in lst]
    flat_b = [t for lst in mb.texts_seen_by("a") + mb.texts_seen_by("b") for t in lst]
    assert not any("TWO" in t for t in flat_a)
    assert not any("ONE" in t for t in flat_b)
    assert wa.get_last_checkpoint_id() != wb.get_last_checkpoint_id()


# Case 15a: restoring into a workflow built with the same executor ids works.
async def test_restore_with_same_ids_continues():
    storage = InMemoryCheckpointStorage()
    wf = fresh(Models(answers={"a": "A", "b": "B"}), storage)
    await wf.run("go", **RUN)
    cid = wf.get_last_checkpoint_id()
    m2 = Models(answers={"a": "A", "b": "B"})
    result = await fresh(m2, storage).run(checkpoint_id=cid, checkpoint_storage=storage, **RUN)
    assert result is not None


# Case 15b: restoring into a workflow with different executor ids fails loudly, not silently.
async def test_restore_with_different_ids_fails_loudly():
    storage = InMemoryCheckpointStorage()
    wf = fresh(Models(answers={"a": "A", "b": "B"}), storage)
    await wf.run("go", **RUN)
    cid = wf.get_last_checkpoint_id()
    m2 = Models()
    with pytest.raises(Exception):
        await fresh(m2, storage, roles=("x", "y")).run(checkpoint_id=cid, checkpoint_storage=storage, **RUN)
    assert m2.roles() == []


# Case 16: two users with the same workflow name cannot see or load each other's checkpoints.
async def test_tenants_cannot_read_each_others_checkpoints():
    inner = InMemoryCheckpointStorage()
    store_a = TenantCheckpointStorage(inner, tenant_id="scbx", user_id="alice")
    store_b = TenantCheckpointStorage(inner, tenant_id="autox", user_id="alice")  # same user id, other PortCo
    wf = fresh(Models(answers={"a": "A", "b": "B"}), store_a)
    await wf.run("secret-prompt", **RUN)
    cid = wf.get_last_checkpoint_id()
    assert len(await store_a.list_checkpoints(workflow_name=NAME)) > 0
    assert await store_b.list_checkpoints(workflow_name=NAME) == []
    with pytest.raises(Exception):
        await store_b.load(cid)
    assert (await store_b.delete(cid)) is False
    assert (await store_a.load(cid)).checkpoint_id == cid


# Case 16b: a blank tenant or user id is rejected, since it would collapse every caller into one partition.
@pytest.mark.parametrize("tenant,user", [("", "u"), ("t", ""), ("  ", "u"), (None, "u")])
def test_blank_tenant_or_user_rejected(tenant, user):
    with pytest.raises(ValueError):
        TenantCheckpointStorage(InMemoryCheckpointStorage(), tenant_id=tenant, user_id=user)


# Case 16c: two users in the SAME tenant are also isolated (the partition must include the user id).
async def test_users_in_same_tenant_cannot_read_each_others_checkpoints():
    inner = InMemoryCheckpointStorage()
    alice = TenantCheckpointStorage(inner, tenant_id="scbx", user_id="alice")
    bob = TenantCheckpointStorage(inner, tenant_id="scbx", user_id="bob")
    wf = fresh(Models(answers={"a": "A", "b": "B"}), alice)
    await wf.run("alice-secret", **RUN)
    cid = wf.get_last_checkpoint_id()
    assert await bob.list_checkpoints(workflow_name=NAME) == []
    with pytest.raises(Exception):
        await bob.load(cid)
    assert (await bob.delete(cid)) is False
    assert (await alice.load(cid)).checkpoint_id == cid


# Case 16d: the raw tenant and user ids are never written into the inner store.
async def test_raw_ids_are_not_stored_in_the_inner_store():
    inner = InMemoryCheckpointStorage()
    store = TenantCheckpointStorage(inner, tenant_id="scbx-tenant", user_id="alice@scbx.com")
    wf = fresh(Models(answers={"a": "A", "b": "B"}), store)
    await wf.run("go", **RUN)
    for cid in await inner.list_checkpoint_ids(workflow_name=f"{store._partition}/{NAME}"):
        raw = repr(await inner.load(cid))
        assert "scbx-tenant" not in raw and "alice@scbx.com" not in raw


# Case 16e: ("a","bc") and ("ab","c") must not map to the same partition.
def test_tenant_user_boundary_cannot_collide():
    inner = InMemoryCheckpointStorage()
    one = TenantCheckpointStorage(inner, tenant_id="a", user_id="bc")
    two = TenantCheckpointStorage(inner, tenant_id="ab", user_id="c")
    assert one._partition != two._partition
