"""Exercise the same backend as the GUI using a headless Qt event loop."""

import argparse
import multiprocessing as mp
import time

from PySide6.QtCore import QCoreApplication, QTimer

from main import Backend


def run_headless(num_procs, seconds, deadlock=False, ipc_index=1, stdout=False):
    app = QCoreApplication.instance() or QCoreApplication([])
    backend = Backend()

    def report_metrics():
        if backend.processMetrics:
            print(f"Per-process CPU utilization (%), resident memory (MiB): {backend.processMetrics}", flush=True)

    backend.processMetricsChanged.connect(report_metrics)
    if deadlock:
        backend.toggleDeadlock()
        ipc_index = 2
    backend.start(num_procs, ipc_index)
    if not backend.running:
        print(backend.alert or backend.status)
        return 1

    QTimer.singleShot(round(seconds * 1000), app.quit)
    try:
        app.exec()
    finally:
        backend.stop()

    if stdout:
        print(backend.timeline, end="")
    else:
        filename = f"ipcsync_log_{int(time.time())}.txt"
        with open(filename, "w", encoding="utf-8") as log:
            log.write(backend.timeline)
        print(f"Headless run finished; log written to {filename}")
    return 0


def parse_args():
    parser = argparse.ArgumentParser(description="Run a short headless IPC simulation")
    parser.add_argument("--procs", type=int, default=3, help="Number of workers to spawn")
    parser.add_argument("--seconds", type=float, default=5, help="Simulation duration")
    parser.add_argument("--ipc", choices=("pipe", "queue", "shared"), default="queue")
    parser.add_argument("--deadlock", action="store_true", help="Run the four-worker Shared Memory demo")
    parser.add_argument("--stdout", action="store_true", help="Print the timeline to standard output")
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    return args


if __name__ == "__main__":
    mp.freeze_support()
    args = parse_args()
    raise SystemExit(run_headless(args.procs, args.seconds, args.deadlock,
                                  ("pipe", "queue", "shared").index(args.ipc), args.stdout))
