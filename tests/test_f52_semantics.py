import unittest

from autopilot.f52_semantics import (
    FabricInvariantError,
    Provider,
    ProviderResult,
    Task,
    accept_result,
    checkpoint,
    durable_requeue,
    restore_task,
    select_provider,
    snapshot_task,
)


class F52SyntheticSemanticsTests(unittest.TestCase):
    def task(self) -> Task:
        return Task(
            task_id="task-42",
            generation=7,
            required_capabilities=frozenset({"tools", "checkpoint"}),
        )

    def test_routing_requires_health_and_all_capabilities(self) -> None:
        task = self.task()
        providers = [
            Provider("a-unhealthy", frozenset({"tools", "checkpoint"}), healthy=False),
            Provider("b-incomplete", frozenset({"tools"}), healthy=True),
            Provider("c-eligible", frozenset({"tools", "checkpoint", "extra"}), healthy=True),
        ]
        self.assertEqual(select_provider(task, providers).provider_id, "c-eligible")

    def test_routing_fails_closed_without_eligible_provider(self) -> None:
        with self.assertRaisesRegex(FabricInvariantError, "no healthy provider"):
            select_provider(
                self.task(),
                [Provider("only", frozenset({"tools"}), healthy=True)],
            )

    def test_result_integrity_rejects_wrong_identity_generation_and_fence(self) -> None:
        task = self.task()
        base = dict(
            task_id=task.task_id,
            generation=task.generation,
            fence=task.fence,
            provider_id="provider-a",
            result_digest="sha256:abc",
        )
        for field, bad in (("task_id", "other"), ("generation", 8), ("fence", 1)):
            candidate = dict(base)
            candidate[field] = bad
            with self.subTest(field=field), self.assertRaises(FabricInvariantError):
                accept_result(task, ProviderResult(**candidate))

    def test_checkpoint_identity_survives_snapshot_restore_and_requeue(self) -> None:
        task = checkpoint(self.task(), "cp-001")
        restored = restore_task(snapshot_task(task))
        requeued = durable_requeue(restored)
        self.assertEqual(requeued.task_id, task.task_id)
        self.assertEqual(requeued.generation, task.generation)
        self.assertEqual(requeued.checkpoint_id, "cp-001")
        self.assertEqual(requeued.attempt, 1)
        self.assertEqual(requeued.fence, 1)

    def test_checkpoint_identity_cannot_be_silently_replaced(self) -> None:
        task = checkpoint(self.task(), "cp-001")
        with self.assertRaisesRegex(FabricInvariantError, "cannot be silently replaced"):
            checkpoint(task, "cp-002")

    def test_requeue_fences_old_attempt_result(self) -> None:
        task = durable_requeue(checkpoint(self.task(), "cp-001"))
        stale = ProviderResult(
            task_id=task.task_id,
            generation=task.generation,
            fence=0,
            provider_id="provider-a",
            result_digest="sha256:old",
            checkpoint_id="cp-001",
        )
        with self.assertRaisesRegex(FabricInvariantError, "fence"):
            accept_result(task, stale)

    def test_exact_completion_replay_is_idempotent_but_conflict_is_rejected(self) -> None:
        task = durable_requeue(checkpoint(self.task(), "cp-001"))
        result = ProviderResult(
            task_id=task.task_id,
            generation=task.generation,
            fence=task.fence,
            provider_id="provider-b",
            result_digest="sha256:new",
            checkpoint_id="cp-001",
        )
        completed = accept_result(task, result)
        self.assertIs(accept_result(completed, result), completed)
        conflicting = ProviderResult(
            task_id=task.task_id,
            generation=task.generation,
            fence=task.fence,
            provider_id="provider-c",
            result_digest="sha256:other",
            checkpoint_id="cp-001",
        )
        with self.assertRaisesRegex(FabricInvariantError, "duplicate completion"):
            accept_result(completed, conflicting)

    def test_cross_provider_continuation_preserves_fence_and_checkpoint(self) -> None:
        task = checkpoint(self.task(), "cp-qwen")
        first = select_provider(
            task,
            [
                Provider("qwen", frozenset({"tools", "checkpoint"}), healthy=True),
                Provider("notion", frozenset({"tools", "checkpoint"}), healthy=True),
            ],
        )
        self.assertEqual(first.provider_id, "notion")

        requeued = durable_requeue(restore_task(snapshot_task(task)))
        second = select_provider(
            requeued,
            [
                Provider("qwen", frozenset({"tools", "checkpoint"}), healthy=True),
                Provider("notion", frozenset({"tools", "checkpoint"}), healthy=False),
            ],
        )
        self.assertEqual(second.provider_id, "qwen")
        result = ProviderResult(
            task_id=requeued.task_id,
            generation=requeued.generation,
            fence=requeued.fence,
            provider_id=second.provider_id,
            result_digest="sha256:continued",
            checkpoint_id=requeued.checkpoint_id,
        )
        completed = accept_result(requeued, result)
        self.assertTrue(completed.completed)
        self.assertEqual(completed.checkpoint_id, "cp-qwen")

    def test_restore_fails_closed_on_shape_and_completion_mismatch(self) -> None:
        snapshot = snapshot_task(self.task())
        snapshot["unexpected"] = True
        with self.assertRaisesRegex(FabricInvariantError, "shape"):
            restore_task(snapshot)

        snapshot = snapshot_task(self.task())
        snapshot["completed"] = True
        with self.assertRaisesRegex(FabricInvariantError, "completion/result"):
            restore_task(snapshot)


if __name__ == "__main__":
    unittest.main()
