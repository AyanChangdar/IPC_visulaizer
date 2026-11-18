"""Process lifecycle, monitoring and worker events shared by desktop and web."""

import multiprocessing as mp
import time
from dataclasses import dataclass
from queue import Empty

import psutil

from diagnostics import Diagnostics, LockAcquired, LockWait
from ipc_engine import PipeChannel, QueueChannel, SharedMemoryChannel
from worker import process_worker


CHANNELS = {
    "pipe": PipeChannel,
    "queue": QueueChannel,
    "shared_memory": SharedMemoryChannel,
}


class SimulationConfigurationError(ValueError):
    """The requested workers and IPC method cannot run together."""


class SimulationRunningError(RuntimeError):
    """A simulation already owns the workers."""


@dataclass(frozen=True)
class WorkerExited:
    index: int
    pid: int
    exit_code: int


def process_record(index, pid, alive):
    return dict(index=index, pid=pid, cpu=None, memory=None, status=None, alive=alive)


class ProcessMonitor:
    def __init__(self):
        self.handles = {}
        self.cpu_primed = set()

    def sample(self, workers):
        return [self._sample_worker(*worker) for worker in workers]

    def _sample_worker(self, index, pid, alive):
        record = process_record(index, pid, alive)
        if not alive:
            self.handles.pop(pid, None)
            self.cpu_primed.discard(pid)
            return record

        try:
            process = self.handles.get(pid)
            if process is None:
                process = self.handles[pid] = psutil.Process(pid)
            if not process.is_running():
                record["alive"] = False
                return record

            with process.oneshot():
                try:
                    record["status"] = process.status()
                except psutil.AccessDenied:
                    pass
                if record["status"] == psutil.STATUS_ZOMBIE:
                    record["alive"] = False
                    return record

                try:
                    record["memory"] = process.memory_info().rss / (1024 * 1024)
                except psutil.AccessDenied:
                    pass
                try:
                    cpu = process.cpu_percent(interval=None)
                    if pid in self.cpu_primed:
                        record["cpu"] = cpu
                    self.cpu_primed.add(pid)
                except psutil.AccessDenied:
                    pass
        except psutil.ZombieProcess:
            record = process_record(index, pid, False)
            record["status"] = psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            record = process_record(index, pid, False)
        except psutil.AccessDenied:
            pass

        return record


def event_text(event):
    if isinstance(event, str):
        return event
    if isinstance(event, LockAcquired):
        return f"[P{event.index}] Acquired Lock {event.lock}."
    if isinstance(event, LockWait):
        return f"[P{event.index}] Waiting for Lock {event.requested} while holding Lock {event.held}."
    if isinstance(event, WorkerExited):
        return f"[P{event.index}] OS process {event.pid} exited with code {event.exit_code}."

    message, latency = event
    return f"[P{message.dst}] Received {message.data!r} from P{message.src}, latency={latency:.4f}s"


class Simulation:
    """Own one worker set. The caller serializes operations on this object."""

    def __init__(self):
        self._ctx = mp.get_context("spawn")
        self.processes = []
        self._stop_writers = []
        self._log_queue = None
        self._demo = None
        self._reported_exits = set()
        self.channel = None
        self.diagnostics = None
        self.method = None
        self.message_count = 0
        self.total_latency = 0.0
        self._started = 0.0

    @property
    def running(self):
        return self.method is not None

    def start(self, count, method, deadlock=False):
        if self.running:
            raise SimulationRunningError("A simulation is already running.")
        if count < 1:
            raise SimulationConfigurationError("At least one worker is required.")
        if method not in CHANNELS:
            raise SimulationConfigurationError("Unknown IPC method.")
        if deadlock and (count < 4 or method != "shared_memory"):
            raise SimulationConfigurationError("Deadlock demo requires Shared Memory and at least four workers.")

        self.message_count = 0
        self.total_latency = 0.0
        try:
            self.channel = CHANNELS[method](count, self._ctx)
            self._log_queue = self._ctx.Queue()
            if deadlock:
                self._demo = ((self._ctx.Lock(), self._ctx.Lock()), self._ctx.Barrier(2))
                self.diagnostics = Diagnostics()

            for index in range(count):
                stop_reader, stop_writer = self._ctx.Pipe(duplex=False)
                self._stop_writers.append(stop_writer)
                try:
                    process = self._ctx.Process(
                        target=process_worker,
                        args=(index, count, self.channel, stop_reader, self._log_queue,
                              self._demo if index in (2, 3) else None),
                        daemon=True,
                    )
                    process.start()
                    self.processes.append(process)
                finally:
                    stop_reader.close()
        except (OSError, RuntimeError, ValueError):
            self.stop()
            raise

        self._started = time.monotonic()
        self.method = method

    def stop(self):
        for writer in self._stop_writers:
            writer.close()
        self._stop_writers.clear()

        events = []
        deadline = time.monotonic() + 0.35
        while any(process.is_alive() for process in self.processes) and time.monotonic() < deadline:
            events.extend(self.poll_events())
            time.sleep(0.01)
        events.extend(self.poll_events())

        for process in self.processes:
            if process.is_alive():
                process.terminate()

        deadline = time.monotonic() + 1
        for process in self.processes:
            process.join(timeout=max(0, deadline - time.monotonic()))
            if process.is_alive():
                process.kill()
                process.join()

        # Read exit codes before closing the handles. A forced exit may leave
        # a partially written log entry, so use the process handles here.
        events.extend(self._worker_exits())
        for process in self.processes:
            process.close()
        self.processes.clear()
        self._reported_exits.clear()
        if self._log_queue is not None:
            self._log_queue.close()
            self._log_queue = None
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        self.method = None
        self._demo = None
        self.diagnostics = None
        return events

    def worker_snapshot(self):
        return [(index, process.pid, process.exitcode is None)
                for index, process in enumerate(self.processes)]

    def poll_events(self):
        events = []
        if self._log_queue is None:
            return events

        for _ in range(256):
            try:
                event = self._log_queue.get_nowait()
            except (Empty, EOFError, OSError):
                break

            if isinstance(event, LockAcquired):
                self.diagnostics.record_acquisition(event)
            elif isinstance(event, LockWait):
                self.diagnostics.record_wait(event)
            elif isinstance(event, tuple):
                self.message_count += 1
                self.total_latency += event[1]
            events.append(event)

        events.extend(self._worker_exits())
        if self.diagnostics is not None:
            self.diagnostics.retain_workers({index for index, _, alive in self.worker_snapshot() if alive})
        return events

    def _worker_exits(self):
        events = []
        for index, process in enumerate(self.processes):
            exit_code = process.exitcode
            if exit_code is not None and index not in self._reported_exits:
                self._reported_exits.add(index)
                events.append(WorkerExited(index, process.pid, exit_code))
        return events

    def performance(self):
        if not self.running:
            return dict(delivered=0, throughput=0.0, average_latency_ms=0.0)

        elapsed = time.monotonic() - self._started
        return dict(
            delivered=self.message_count,
            throughput=self.message_count / elapsed if elapsed > 0 else 0.0,
            average_latency_ms=1000 * self.total_latency / self.message_count if self.message_count else 0.0,
        )
