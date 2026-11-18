import multiprocessing as mp
import time
import unittest
from unittest.mock import patch

import psutil
from PySide6.QtCore import QCoreApplication

from main import Backend, ProcessMonitor


def burn_cpu(ready):
    ready.set()
    while True:
        pass


class TestBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.backend = Backend()
        self.addCleanup(self.backend.stop)

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail("Timed out waiting for backend state")

    def test_worker_pids_metrics_updates_and_external_exit(self):
        samples = []
        self.backend.processMetricsChanged.connect(lambda: samples.append(self.backend.processMetrics))
        self.backend.start(4, 0)

        pids = [process.pid for process in self.backend.processes]
        self.assertEqual(len(set(pids)), 4)
        self.assertEqual([record["pid"] for record in self.backend.processMetrics], pids)
        self.assertTrue(set(pids).issubset({child.pid for child in psutil.Process().children()}))

        def all_measured():
            return all(record["cpu"] is not None and record["memory"] is not None
                       for record in self.backend.processMetrics)

        self.wait_until(all_measured)
        self.assertGreaterEqual(len(samples), 3)
        for index, record in enumerate(self.backend.processMetrics):
            self.assertEqual(set(record), {"index", "pid", "cpu", "memory", "status", "alive"})
            self.assertEqual(record["index"], index)
            self.assertIsInstance(record["cpu"], float)
            self.assertGreater(record["memory"], 0)
            self.assertIsInstance(record["status"], str)
            self.assertTrue(record["alive"])

        victim = self.backend.processes[1]
        victim.terminate()
        victim.join(timeout=5)
        self.assertFalse(victim.is_alive())
        self.wait_until(lambda: not self.backend.processMetrics[1]["alive"])
        self.assertEqual(self.backend.processMetrics[1], {
            "index": 1, "pid": pids[1], "cpu": None, "memory": None, "status": None, "alive": False,
        })

        self.backend.stop()
        self.assertFalse(self.backend.process_timer.isActive())
        self.assertEqual(samples[-1], [])
        self.assertTrue(all(not psutil.pid_exists(pid) for pid in pids))

    def test_two_lock_cycle_is_reported_and_stop_recovers(self):
        self.backend.toggleDeadlock()
        self.backend.start(4, 2)
        pids = [process.pid for process in self.backend.processes]
        self.wait_until(lambda: self.backend.frozenProcesses == [2, 3])
        self.assertTrue(self.backend.alert.startswith("DEADLOCK:"))
        self.assertIn("P2 owns Lock A and waits for Lock B", self.backend.alert)
        self.assertIn("P3 owns Lock B and waits for Lock A", self.backend.alert)
        self.assertIn("[P2] Acquired Lock A.", self.backend.timeline)
        self.assertIn("[P3] Acquired Lock B.", self.backend.timeline)
        self.assertIn("[P2] Waiting for Lock B while holding Lock A.", self.backend.timeline)
        self.assertIn("[P3] Waiting for Lock A while holding Lock B.", self.backend.timeline)
        self.assertTrue(all(process.is_alive() for process in self.backend.processes))

        self.backend.stop()
        self.assertFalse(self.backend.deadlockActive)
        self.assertEqual(self.backend.frozenProcesses, [])
        self.assertEqual(self.backend.alert, "")
        self.assertEqual(self.backend.processMetrics, [])
        self.assertTrue(all(not psutil.pid_exists(pid) for pid in pids))

        self.backend.start(4, 2)
        self.wait_until(lambda: self.backend.message_count > 0)
        self.assertEqual(self.backend.frozenProcesses, [])
        self.assertEqual(self.backend.alert, "")

    def test_deadlock_demo_requires_four_workers(self):
        self.backend.toggleDeadlock()
        self.backend.start(3, 2)
        self.assertFalse(self.backend.running)
        self.assertIn("at least four workers", self.backend.alert)
        self.assertEqual(self.backend.processes, [])
        self.assertEqual(self.backend.frozenProcesses, [])

    def test_repeated_start_stop_and_delivery_signals_for_all_channels(self):
        flow = []
        self.backend.dataFlow.connect(lambda src, dst: flow.append((src, dst)))
        for repeat in range(2):
            for channel in range(3):
                with self.subTest(repeat=repeat, channel=channel):
                    flow.clear()
                    self.backend.start(4, channel)
                    pids = [process.pid for process in self.backend.processes]
                    try:
                        self.assertTrue(self.backend.running)
                        self.assertEqual(len(pids), 4)
                        self.wait_until(lambda: bool(flow))
                        src, dst = flow[0]
                        self.assertNotEqual(src, dst)
                        self.assertIn(f"[P{dst}] Received 'Hello from P{src}' from P{src}", self.backend.timeline)
                    finally:
                        self.backend.stop()
                    self.assertFalse(self.backend.running)
                    self.assertEqual(self.backend.processMetrics, [])
                    self.assertNotIn("Channel closed", self.backend.timeline)
                    self.assertTrue(all(not psutil.pid_exists(pid) for pid in pids))
                    for index in range(4):
                        self.assertIn(f"[P{index}] Stopping.", self.backend.timeline)


class TestProcessMonitor(unittest.TestCase):
    def test_cpu_is_primed_then_measured_from_running_worker(self):
        ctx = mp.get_context("spawn")
        ready = ctx.Event()
        worker = ctx.Process(target=burn_cpu, args=(ready,))
        worker.start()
        try:
            self.assertTrue(ready.wait(timeout=8))
            monitor = ProcessMonitor()
            snapshot = [(0, worker.pid, True)]
            self.assertIsNone(monitor.sample(snapshot)[0]["cpu"])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                time.sleep(0.2)
                record = monitor.sample(snapshot)[0]
                if record["cpu"] > 0:
                    break
            self.assertGreater(record["cpu"], 0)
            self.assertGreater(record["memory"], 0)
        finally:
            worker.terminate()
            worker.join()
            worker.close()

    def test_psutil_exceptions(self):
        for error, alive, status in (
            (psutil.NoSuchProcess(101), False, None),
            (psutil.ZombieProcess(101), False, psutil.STATUS_ZOMBIE),
            (psutil.AccessDenied(101), True, None),
        ):
            with self.subTest(error=error), patch("main.psutil.Process", side_effect=error):
                self.assertEqual(ProcessMonitor().sample([(0, 101, True)]), [{
                    "index": 0, "pid": 101, "cpu": None, "memory": None,
                    "status": status, "alive": alive,
                }])


if __name__ == "__main__":
    unittest.main()
