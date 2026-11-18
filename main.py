import multiprocessing as mp
import sys
import time
from queue import Empty

import psutil
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from diagnostics import Diagnostics, LockWait
from ipc_engine import PipeChannel, QueueChannel, SharedMemoryChannel
from worker import process_worker


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


class SampleSignals(QObject):
    ready = Signal(object, object)


class SampleProcesses(QRunnable):
    def __init__(self, monitor, workers):
        super().__init__()
        self.monitor = monitor
        self.workers = workers
        self.signals = SampleSignals()

    def run(self):
        self.signals.ready.emit(self.monitor, self.monitor.sample(self.workers))


class Backend(QObject):
    """Run multiprocessing workers and expose their activity to QML."""

    dataFlow = Signal(int, int)
    timelineChanged = Signal()
    runningChanged = Signal()
    statusChanged = Signal()
    alertChanged = Signal()
    metricsChanged = Signal()
    deadlockActiveChanged = Signal()
    channelTypeChanged = Signal()
    frozenProcessesChanged = Signal()
    processMetricsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ctx = mp.get_context("spawn")
        self._running = False
        self._status = "Idle"
        self._timeline = "Welcome to IPCSync Debugger!\n"
        self._alert = ""
        self._throughput = "Throughput: 0.0 msg/s"
        self._latency = "Avg. Latency: 0.00 ms"
        self._deadlock_active = False
        self._channel_type = "Pipe"
        self._frozen_processes = []
        self._process_metrics = []
        self._monitor = None
        self._sampling = False

        self.processes = []
        self.log_queue = None
        self._stop_writers = []
        self._demo = None
        self.channel = None
        self.diagnostics = None

        self.message_count = 0
        self.total_latency = 0.0
        self.start_time = 0.0

        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.update_logs)
        self.log_timer.start(100)

        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.update_stats)
        self.stats_timer.start(500)

        self.process_timer = QTimer(self)
        self.process_timer.setInterval(1000)
        self.process_timer.timeout.connect(self._request_process_sample)

    @Property(bool, notify=runningChanged)
    def running(self):
        return self._running

    @Property(str, notify=statusChanged)
    def status(self):
        return self._status

    @Property(str, notify=timelineChanged)
    def timeline(self):
        return self._timeline

    @Property(str, notify=alertChanged)
    def alert(self):
        return self._alert

    @Property(str, notify=metricsChanged)
    def throughput(self):
        return self._throughput

    @Property(str, notify=metricsChanged)
    def latency(self):
        return self._latency

    @Property(bool, notify=deadlockActiveChanged)
    def deadlockActive(self):
        return self._deadlock_active

    @Property(str, notify=channelTypeChanged)
    def channelType(self):
        return self._channel_type

    @Property("QVariantList", notify=frozenProcessesChanged)
    def frozenProcesses(self):
        return self._frozen_processes

    @Property("QVariantList", notify=processMetricsChanged)
    def processMetrics(self):
        return self._process_metrics

    @Slot(int, int)
    def start(self, num_procs: int, ipc_index: int):
        if self._running:
            self.stop()
            return

        ipc_map = {0: PipeChannel, 1: QueueChannel, 2: SharedMemoryChannel}
        channel_class = ipc_map.get(ipc_index, PipeChannel)
        if self._deadlock_active and (num_procs < 4 or channel_class is not SharedMemoryChannel):
            self._set_alert("Deadlock demo requires Shared Memory and at least four workers.")
            return
        if num_procs < 1:
            self.update_status("At least one worker is required.")
            return

        self._set_alert("")
        self.message_count = 0
        self.total_latency = 0.0
        self._throughput = "Throughput: 0.0 msg/s"
        self._latency = "Avg. Latency: 0.00 ms"
        self.metricsChanged.emit()
        self._frozen_processes = []
        self.frozenProcessesChanged.emit()

        try:
            self.channel = channel_class(num_procs, self._ctx)
            self.log_queue = self._ctx.Queue()
            if self._deadlock_active:
                self._demo = ((self._ctx.Lock(), self._ctx.Lock()), self._ctx.Barrier(2))
                self.diagnostics = Diagnostics()

            self._channel_type = self.channel.type_name()
            self.channelTypeChanged.emit()
            self._timeline = (
                f"--- Starting simulation with {num_procs} processes using {self._channel_type} ---\n"
            )
            if self._demo:
                self._timeline += "--- Deadlock demo: P2 and P3 each take one lock ---\n"
            self.timelineChanged.emit()

            for index in range(num_procs):
                stop_reader, stop_writer = self._ctx.Pipe(duplex=False)
                self._stop_writers.append(stop_writer)
                try:
                    process = self._ctx.Process(
                        target=process_worker,
                        args=(index, num_procs, self.channel, stop_reader, self.log_queue,
                              self._demo if index in (2, 3) else None),
                        daemon=True,
                    )
                    process.start()
                    self.processes.append(process)
                finally:
                    stop_reader.close()
        except (OSError, RuntimeError, ValueError) as exc:
            self._stop_workers()
            self.update_status(f"Could not start workers: {exc}")
            return

        self.start_time = time.time()
        self._running = True
        self._monitor = ProcessMonitor()
        self._process_metrics = [process_record(*worker) for worker in self._worker_snapshot()]
        self.processMetricsChanged.emit()
        self._request_process_sample()
        self.process_timer.start()
        self.runningChanged.emit()
        self.update_status("Running deadlock demo..." if self._demo else "Running...")

    @Slot()
    def stop(self):
        if not self._running:
            return

        self.update_status("Stopping...")
        self.process_timer.stop()
        self._monitor = None
        self._sampling = False
        self._stop_workers()

        self._process_metrics = []
        self.processMetricsChanged.emit()
        self._frozen_processes = []
        self.frozenProcessesChanged.emit()
        self._set_alert("")

        if self._deadlock_active:
            self._deadlock_active = False
            self.deadlockActiveChanged.emit()

        self._timeline += "--- Simulation Stopped ---\n"
        self.timelineChanged.emit()
        self._running = False
        self.runningChanged.emit()
        self.update_status("Idle")

    def _stop_workers(self):
        for writer in self._stop_writers:
            writer.close()
        self._stop_writers.clear()

        # Give cooperative workers one shared grace period and drain their log feeds.
        deadline = time.monotonic() + 0.35
        while any(process.is_alive() for process in self.processes) and time.monotonic() < deadline:
            self.update_logs()
            time.sleep(0.01)
        self.update_logs()

        for process in self.processes:
            if process.is_alive():
                process.terminate()
        deadline = time.monotonic() + 1
        for process in self.processes:
            process.join(timeout=max(0, deadline - time.monotonic()))
            if process.is_alive():
                process.kill()
                process.join()
            process.close()

        self.processes.clear()
        if self.log_queue is not None:
            self.log_queue.close()
            self.log_queue = None
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        self._demo = None
        self.diagnostics = None

    @Slot()
    def exportLog(self):
        try:
            filename = f"ipcsync_log_{int(time.time())}.txt"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(self._timeline)
            self.update_status(f"Log exported to {filename}")
        except OSError as exc:
            self.update_status(f"Error exporting log: {exc}")

    @Slot()
    def toggleDeadlock(self):
        if self._running:
            self.update_status("Cannot change deadlock mode while running.")
            return

        self._deadlock_active = not self._deadlock_active
        self.deadlockActiveChanged.emit()
        self._set_alert("")
        self.update_status("Deadlock mode ON" if self._deadlock_active else "Deadlock mode OFF")

    def _worker_snapshot(self):
        return [(index, process.pid, process.exitcode is None)
                for index, process in enumerate(self.processes)]

    @Slot()
    def _request_process_sample(self):
        if self._monitor is None or self._sampling:
            return

        self._sampling = True
        task = SampleProcesses(self._monitor, self._worker_snapshot())
        task.signals.ready.connect(self._publish_process_sample)
        QThreadPool.globalInstance().start(task)

    @Slot(object, object)
    def _publish_process_sample(self, monitor, records):
        if monitor is not self._monitor:
            return

        self._sampling = False
        self._process_metrics = records
        self.processMetricsChanged.emit()

    def update_status(self, msg):
        if msg != self._status:
            self._status = msg
            self.statusChanged.emit()

    def _set_alert(self, msg):
        if msg != self._alert:
            self._alert = msg
            self.alertChanged.emit()

    def update_logs(self):
        if self.log_queue is None:
            return

        lines = []
        for _ in range(256):
            try:
                event = self.log_queue.get_nowait()
            except (Empty, EOFError, OSError):
                break

            if isinstance(event, LockWait):
                lines.append(f"[P{event.index}] Waiting for Lock {event.requested} while holding Lock {event.held}.")
                if self.diagnostics is not None:
                    self.diagnostics.record_wait(event)
            elif isinstance(event, str):
                lines.append(event)
            else:
                message, latency = event
                self.total_latency += latency
                self.message_count += 1
                lines.append(f"[P{message.dst}] Received {message.data!r} from P{message.src}, latency={latency:.4f}s")
                self.dataFlow.emit(message.src, message.dst)

        self._update_deadlock(lines)
        if lines:
            self._timeline += "\n".join(lines) + "\n"
            self.timelineChanged.emit()

    def _update_deadlock(self, lines):
        if self.diagnostics is None:
            return

        alive = {index for index, _, alive in self._worker_snapshot() if alive}
        self.diagnostics.retain_workers(alive)
        if self.diagnostics.blocked != self._frozen_processes:
            self._frozen_processes = self.diagnostics.blocked
            self.frozenProcessesChanged.emit()
        if self.diagnostics.alert and self.diagnostics.alert != self._alert:
            lines.append(self.diagnostics.alert)
        self._set_alert(self.diagnostics.alert)

    def update_stats(self):
        if not self._running:
            return

        if self.diagnostics is None:
            self.update_status(self.channel.status())
        elapsed = time.time() - self.start_time
        if elapsed > 0:
            self._throughput = f"Throughput: {self.message_count / elapsed:.1f} msg/s"
            latency = 1000 * self.total_latency / self.message_count if self.message_count else 0.0
            self._latency = f"Avg. Latency: {latency:.2f} ms"
            self.metricsChanged.emit()


def main():
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    backend = Backend()

    engine.rootContext().setContextProperty("backend", backend)
    engine.load("gui.qml")
    if not engine.rootObjects():
        sys.exit(-1)

    app.aboutToQuit.connect(backend.stop)
    sys.exit(app.exec())


if __name__ == "__main__":
    mp.freeze_support()
    main()
