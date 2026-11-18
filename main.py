import multiprocessing as mp
import sys
import time
from PySide6.QtCore import QObject, Property, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from simulation import Simulation, SimulationConfigurationError, event_text, process_record, ProcessMonitor


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
        self.simulation = Simulation()
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

    @property
    def processes(self):
        return self.simulation.processes

    @property
    def message_count(self):
        return self.simulation.message_count

    @Slot(int, int)
    def start(self, num_procs: int, ipc_index: int):
        if self._running:
            self.stop()
            return

        method = {0: "pipe", 1: "queue", 2: "shared_memory"}.get(ipc_index, "pipe")
        try:
            self.simulation.start(num_procs, method, self._deadlock_active)
        except SimulationConfigurationError as exc:
            self._set_alert(str(exc))
            return
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status(f"Could not start workers: {exc}")
            return

        self._set_alert("")
        self._throughput = "Throughput: 0.0 msg/s"
        self._latency = "Avg. Latency: 0.00 ms"
        self.metricsChanged.emit()
        self._channel_type = self.simulation.channel.type_name()
        self.channelTypeChanged.emit()
        self._timeline = (
            f"--- Starting simulation with {num_procs} processes using {self._channel_type} ---\n"
        )
        if self._deadlock_active:
            self._timeline += "--- Deadlock demo: P2 and P3 each take one lock ---\n"
        self.timelineChanged.emit()

        self._running = True
        self._monitor = ProcessMonitor()
        self._process_metrics = [process_record(*worker) for worker in self.simulation.worker_snapshot()]
        self.processMetricsChanged.emit()
        self._request_process_sample()
        self.process_timer.start()
        self.runningChanged.emit()
        self.update_status("Running deadlock demo..." if self._deadlock_active else "Running...")

    @Slot()
    def stop(self):
        if not self._running:
            return

        self.update_status("Stopping...")
        self.process_timer.stop()
        self._monitor = None
        self._sampling = False
        self.update_logs()
        self._append_timeline(self.simulation.stop())

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

    @Slot()
    def _request_process_sample(self):
        if self._monitor is None or self._sampling:
            return

        self._sampling = True
        task = SampleProcesses(self._monitor, self.simulation.worker_snapshot())
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
        self._append_timeline(self.simulation.poll_events())

    def _append_timeline(self, events):
        lines = [event_text(event) for event in events]
        for event in events:
            if isinstance(event, tuple):
                message, _ = event
                self.dataFlow.emit(message.src, message.dst)

        diagnostics = self.simulation.diagnostics
        if diagnostics is not None:
            if diagnostics.blocked != self._frozen_processes:
                self._frozen_processes = diagnostics.blocked
                self.frozenProcessesChanged.emit()
            if diagnostics.alert and diagnostics.alert != self._alert:
                lines.append(diagnostics.alert)
            self._set_alert(diagnostics.alert)
        if lines:
            self._timeline += "\n".join(lines) + "\n"
            self.timelineChanged.emit()

    def update_stats(self):
        if not self._running:
            return

        if self.simulation.diagnostics is None:
            self.update_status(self.simulation.channel.status())
        performance = self.simulation.performance()
        self._throughput = f"Throughput: {performance['throughput']:.1f} msg/s"
        self._latency = f"Avg. Latency: {performance['average_latency_ms']:.2f} ms"
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
