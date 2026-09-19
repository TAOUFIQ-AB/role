import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from pipeline import FailureKind, ReelStatus, ReelTask, WorkQueue


class PipelineTests(unittest.TestCase):
    def test_first_transient_failure_is_retryable(self):
        task = ReelTask("https://example/reel/1", "1", max_attempts=2)
        task.mark_retry("timeout", FailureKind.TRANSIENT)
        self.assertEqual(task.attempt, 1)
        self.assertEqual(task.failure_kind, FailureKind.TRANSIENT)
        self.assertEqual(task.status, ReelStatus.RETRY)
        self.assertTrue(task.is_retryable)

    def test_retry_budget_is_not_double_counted_during_queue_move(self):
        queue = WorkQueue()
        task = ReelTask("https://example/reel/2", "2", max_attempts=2)
        task.mark_retry("timeout", FailureKind.TRANSIENT)
        queue.enqueue_retry(task)
        moved = queue.flush_retries()
        self.assertEqual(moved, 1)
        self.assertEqual(task.attempt, 1)
        self.assertEqual(task.status, ReelStatus.PROCESSING)
        self.assertIs(queue.process.get_nowait(), task)

    def test_second_failure_exhausts_two_attempt_budget(self):
        task = ReelTask("https://example/reel/3", "3", max_attempts=2)
        task.mark_retry("timeout", FailureKind.TRANSIENT)
        task.mark_retry("timeout again", FailureKind.TRANSIENT)
        self.assertEqual(task.attempt, 2)
        self.assertEqual(task.status, ReelStatus.FAILED)
        self.assertFalse(task.is_retryable)


if __name__ == "__main__":
    unittest.main()
