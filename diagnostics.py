"""Wait-for graph for lock events reported by the demo workers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LockAcquired:
    index: int
    lock: str


@dataclass(frozen=True)
class LockWait:
    index: int
    held: str
    requested: str


class Diagnostics:
    def __init__(self):
        self.waits = {}
        self.owners = {}
        self.blocked = []
        self.alert = ""

    def record_acquisition(self, event):
        self.owners[event.lock] = event.index
        self.check_deadlock()

    def record_wait(self, event):
        self.waits[event.index] = event
        self.check_deadlock()

    def retain_workers(self, alive):
        self.owners = {lock: index for lock, index in self.owners.items() if index in alive}
        self.waits = {index: event for index, event in self.waits.items() if index in alive}
        self.check_deadlock()

    def check_deadlock(self):
        self.blocked = []
        self.alert = ""
        for start in sorted(self.waits):
            path = []
            index = start
            while index in self.waits and index not in path:
                path.append(index)
                index = self.owners.get(self.waits[index].requested)

            if index in path:
                self.blocked = sorted(path[path.index(index):])
                details = []
                for index in self.blocked:
                    event = self.waits[index]
                    details.append(f"P{index} owns Lock {event.held} and waits for Lock {event.requested}")
                self.alert = "DEADLOCK: " + " | ".join(details)
                return
