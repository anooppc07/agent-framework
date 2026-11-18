"""Tenant-partitioned checkpoint storage.

Wraps any MAF CheckpointStorage. The partition key is sha256(tenant_id, user_id): the raw ids are never
written into the inner store, and one caller can never list, load or delete another caller's checkpoints,
even when both use the same workflow name or the same user id in different PortCo tenants.

Isolation here is enforced by this wrapper, not by the session id. A session id or a checkpoint id alone is
not an authorization boundary: always build this wrapper from the authenticated caller, server-side.
"""
import dataclasses
import hashlib

from agent_framework import CheckpointStorage, WorkflowCheckpoint


def _require(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


class TenantCheckpointStorage:
    def __init__(self, inner: CheckpointStorage, *, tenant_id: str, user_id: str) -> None:
        tenant, user = _require(tenant_id, "tenant_id"), _require(user_id, "user_id")
        self._inner = inner
        # Length-prefix the tenant so ("a", "bc") and ("ab", "c") cannot collide.
        self._partition = hashlib.sha256(f"{len(tenant)}:{tenant}|{user}".encode()).hexdigest()[:32]

    def _scoped(self, workflow_name: str) -> str:
        return f"{self._partition}/{workflow_name}"

    def _owns(self, checkpoint: WorkflowCheckpoint) -> bool:
        return checkpoint.workflow_name.startswith(f"{self._partition}/")

    def _outward(self, checkpoint: WorkflowCheckpoint) -> WorkflowCheckpoint:
        name = checkpoint.workflow_name.split("/", 1)[1]
        return dataclasses.replace(checkpoint, workflow_name=name)

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        return await self._inner.save(dataclasses.replace(checkpoint, workflow_name=self._scoped(checkpoint.workflow_name)))

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        checkpoint = await self._inner.load(checkpoint_id)
        if not self._owns(checkpoint):
            # Same error as a missing id, so a caller cannot probe which ids exist for other tenants.
            raise KeyError(f"checkpoint {checkpoint_id!r} not found")
        return self._outward(checkpoint)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        found = await self._inner.list_checkpoints(workflow_name=self._scoped(workflow_name))
        return [self._outward(c) for c in found if self._owns(c)]

    async def delete(self, checkpoint_id: str) -> bool:
        try:
            checkpoint = await self._inner.load(checkpoint_id)
        except Exception:
            return False
        if not self._owns(checkpoint):
            return False
        return await self._inner.delete(checkpoint_id)
