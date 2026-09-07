from __future__ import annotations

from dataclasses import dataclass, replace
from typing import FrozenSet, Iterable, Mapping


class FabricInvariantError(RuntimeError):
    """Raised when synthetic Fabric semantics would violate a safety invariant."""


@dataclass(frozen=True)
class Provider:
    provider_id: str
    capabilities: FrozenSet[str]
    healthy: bool = True


@dataclass(frozen=True)
class Task:
    task_id: str
    generation: int
    required_capabilities: FrozenSet[str]
    checkpoint_id: str | None = None
    attempt: int = 0
    fence: int = 0
    completed: bool = False
    result_digest: str | None = None


@dataclass(frozen=True)
class ProviderResult:
    task_id: str
    generation: int
    fence: int
    provider_id: str
    result_digest: str
    checkpoint_id: str | None = None


def select_provider(task: Task, providers: Iterable[Provider]) -> Provider:
    """Select one healthy capable provider, failing closed when none qualifies.

    The deterministic provider_id ordering makes the synthetic proof reproducible and
    avoids any implication that provider ordering stands in for live-provider health.
    """

    eligible = sorted(
        (
            provider
            for provider in providers
            if provider.healthy
            and task.required_capabilities.issubset(provider.capabilities)
        ),
        key=lambda provider: provider.provider_id,
    )
    if not eligible:
        raise FabricInvariantError("no healthy provider satisfies required capabilities")
    return eligible[0]


def accept_result(task: Task, result: ProviderResult) -> Task:
    """Fence a provider result to the exact task identity/generation/attempt.

    A completion is immutable. Replays of the exact already-accepted completion are
    idempotent, while any conflicting second completion is rejected.
    """

    if result.task_id != task.task_id:
        raise FabricInvariantError("result task identity mismatch")
    if result.generation != task.generation:
        raise FabricInvariantError("result generation mismatch")
    if result.fence != task.fence:
        raise FabricInvariantError("stale or future result fence")
    if task.completed:
        if task.result_digest == result.result_digest and task.checkpoint_id == result.checkpoint_id:
            return task
        raise FabricInvariantError("conflicting duplicate completion")
    return replace(
        task,
        completed=True,
        result_digest=result.result_digest,
        checkpoint_id=result.checkpoint_id,
    )


def checkpoint(task: Task, checkpoint_id: str) -> Task:
    if task.completed:
        raise FabricInvariantError("cannot checkpoint a completed task")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise FabricInvariantError("checkpoint identity must be non-empty")
    if task.checkpoint_id is not None and task.checkpoint_id != checkpoint_id:
        raise FabricInvariantError("checkpoint identity cannot be silently replaced")
    return replace(task, checkpoint_id=checkpoint_id)


def durable_requeue(task: Task) -> Task:
    """Create the next fenced attempt without changing durable task/checkpoint identity."""

    if task.completed:
        raise FabricInvariantError("completed task cannot be requeued")
    return replace(task, attempt=task.attempt + 1, fence=task.fence + 1)


def restore_task(snapshot: Mapping[str, object]) -> Task:
    """Restore a synthetic durable task snapshot with strict shape validation."""

    required = {
        "task_id",
        "generation",
        "required_capabilities",
        "checkpoint_id",
        "attempt",
        "fence",
        "completed",
        "result_digest",
    }
    if set(snapshot) != required:
        raise FabricInvariantError("snapshot shape mismatch")
    task_id = snapshot["task_id"]
    generation = snapshot["generation"]
    capabilities = snapshot["required_capabilities"]
    checkpoint_id = snapshot["checkpoint_id"]
    attempt = snapshot["attempt"]
    fence = snapshot["fence"]
    completed = snapshot["completed"]
    result_digest = snapshot["result_digest"]
    if not isinstance(task_id, str) or not task_id:
        raise FabricInvariantError("invalid task_id")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        raise FabricInvariantError("invalid generation")
    if not isinstance(capabilities, (list, tuple, set, frozenset)) or not all(
        isinstance(item, str) and item for item in capabilities
    ):
        raise FabricInvariantError("invalid required_capabilities")
    if checkpoint_id is not None and (not isinstance(checkpoint_id, str) or not checkpoint_id):
        raise FabricInvariantError("invalid checkpoint_id")
    if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 0:
        raise FabricInvariantError("invalid attempt")
    if not isinstance(fence, int) or isinstance(fence, bool) or fence < 0:
        raise FabricInvariantError("invalid fence")
    if not isinstance(completed, bool):
        raise FabricInvariantError("invalid completed flag")
    if result_digest is not None and (not isinstance(result_digest, str) or not result_digest):
        raise FabricInvariantError("invalid result_digest")
    if completed != (result_digest is not None):
        raise FabricInvariantError("completion/result mismatch")
    return Task(
        task_id=task_id,
        generation=generation,
        required_capabilities=frozenset(capabilities),
        checkpoint_id=checkpoint_id,
        attempt=attempt,
        fence=fence,
        completed=completed,
        result_digest=result_digest,
    )


def snapshot_task(task: Task) -> dict[str, object]:
    return {
        "task_id": task.task_id,
        "generation": task.generation,
        "required_capabilities": sorted(task.required_capabilities),
        "checkpoint_id": task.checkpoint_id,
        "attempt": task.attempt,
        "fence": task.fence,
        "completed": task.completed,
        "result_digest": task.result_digest,
    }
