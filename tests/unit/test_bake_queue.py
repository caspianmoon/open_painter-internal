"""Bake queue setup/cleanup failures; real Event routing is tested in Blender."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def load_operator():
    # Load the actual methods without registering an operator or leaving a bpy
    # replacement installed for other unit tests (including Blender's runner).
    spec = importlib.util.spec_from_file_location(
        "open_painter.baking._queue_cleanup_test", ROOT / "src/open_painter/baking/jobs.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"bpy": SimpleNamespace(types=SimpleNamespace(Operator=object))}):
        spec.loader.exec_module(module)
    return module.OP_OT_bake_queue


Operator = load_operator()


class Queue:
    finish = Operator.finish
    invoke = Operator.invoke
    _set = Operator._set

    def __init__(self):
        self._job = self._timer = None
        self._last_timer_duration = 0.
        self._set_id, self._set_pointer = "", 0
        self.reports = []

    def report(self, level, message):
        self.reports.append((level, message))


class Job:
    def __init__(self, close_error=None):
        self.close_error = close_error
        self.next_calls = self.close_calls = 0

    def __next__(self):
        self.next_calls += 1
        return "CURVATURE"

    def close(self):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class WindowManager:
    def __init__(self, failure=None):
        self.failure = failure
        self.timer = SimpleNamespace(time_duration=0.)
        self.added = []
        self.handlers = []
        self.removed = []

    def event_timer_add(self, interval, *, window):
        self.added.append((interval, window))
        if self.failure == "timer":
            raise RuntimeError("Timer creation failed")
        return self.timer

    def modal_handler_add(self, operator):
        self.handlers.append(operator)
        if self.failure == "handler":
            raise RuntimeError("Handler registration failed")

    def event_timer_remove(self, timer):
        self.removed.append(timer)
        if self.failure == "removed":
            raise ReferenceError("Timer already removed with its window")


class TextureSet:
    """The captured target: the queue records its id and address (UI plan phase 4)."""
    id = "set-1"

    def as_pointer(self):
        return 4096


def context_for(manager):
    return SimpleNamespace(window_manager=manager, window=object(),
                           scene=SimpleNamespace(open_painter=SimpleNamespace(status="", texture_sets=[TextureSet()])))


class BakeQueueCleanupTests(unittest.TestCase):
    def invoke_with_job(self, queue, context, job):
        texture_set = context.scene.open_painter.texture_sets[0]

        def bake_steps(actual_context, actual_set):
            self.assertIs(actual_context, context)
            self.assertIs(actual_set, texture_set)
            return job

        modules = {
            "open_painter.core.project_manager": SimpleNamespace(require_set=lambda ctx: texture_set),
            "open_painter.baking.bake_manager": SimpleNamespace(bake_steps=bake_steps),
        }
        with patch.dict(sys.modules, modules):
            return queue.invoke(context, SimpleNamespace(type="LEFTMOUSE"))

    def assert_released(self, queue):
        self.assertIsNone(queue._job)
        self.assertIsNone(queue._timer)
        self.assertEqual(queue._last_timer_duration, 0.)

    def test_timer_creation_failure_closes_primed_job(self):
        manager = WindowManager("timer")
        context = context_for(manager)
        queue, job = Queue(), Job()
        self.assertEqual(self.invoke_with_job(queue, context, job), {"CANCELLED"})
        self.assertEqual((job.next_calls, job.close_calls), (1, 1))
        self.assertEqual(manager.added, [(.1, context.window)])
        self.assertEqual(manager.handlers, [])
        self.assertEqual(manager.removed, [])
        self.assertEqual(queue.reports, [({"ERROR"}, "Timer creation failed")])
        self.assert_released(queue)
        self.assertEqual(Operator.running_set_id, "", "a failed start leaves no running set")
        queue.finish(context)
        self.assertEqual(job.close_calls, 1)

    def test_handler_registration_failure_removes_timer_and_closes_job(self):
        manager = WindowManager("handler")
        context = context_for(manager)
        queue, job = Queue(), Job()
        self.assertEqual(self.invoke_with_job(queue, context, job), {"CANCELLED"})
        self.assertEqual((job.next_calls, job.close_calls), (1, 1))
        self.assertEqual(manager.handlers, [queue])
        self.assertEqual(manager.removed, [manager.timer])
        self.assertEqual(queue.reports, [({"ERROR"}, "Handler registration failed")])
        self.assert_released(queue)
        queue.finish(context)
        self.assertEqual(job.close_calls, 1)
        self.assertEqual(manager.removed, [manager.timer])

    def test_close_failure_still_removes_timer_and_detaches_handles(self):
        manager = WindowManager()
        context = context_for(manager)
        queue = Queue()
        job = queue._job = Job(RuntimeError("Job close failed"))
        queue._timer = manager.timer
        queue._last_timer_duration = 3.2
        with self.assertRaisesRegex(RuntimeError, "Job close failed"):
            queue.finish(context)
        self.assertEqual(job.close_calls, 1)
        self.assertEqual(manager.removed, [manager.timer])
        self.assert_released(queue)
        queue.finish(context)
        self.assertEqual(job.close_calls, 1)
        self.assertEqual(manager.removed, [manager.timer])

    def test_already_removed_window_timer_cleanup_is_idempotent(self):
        manager = WindowManager("removed")
        context = context_for(manager)
        queue = Queue()
        job = queue._job = Job()
        queue._timer = manager.timer
        queue.finish(context)
        self.assert_released(queue)
        queue.finish(context)
        self.assertEqual(job.close_calls, 1)
        self.assertEqual(manager.removed, [manager.timer])


if __name__ == "__main__":
    unittest.main()
