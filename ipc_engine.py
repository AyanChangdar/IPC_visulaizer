"""Destination-addressed multiprocessing channels for the simulator."""

import multiprocessing as mp
import time
from dataclasses import dataclass
from queue import Empty
from typing import Optional


@dataclass
class Message:
    src: int
    dst: int
    data: str
    timestamp: float


class IPCChannel:
    """Each worker reads its own inbox. A false send result means retry later."""

    def send(self, src: int, dst: int, data: str) -> bool:
        raise NotImplementedError

    def recv(self, dst: int) -> Optional[Message]:
        raise NotImplementedError

    def status(self) -> str:
        return f"{self.type_name()}: addressed inboxes"

    def type_name(self) -> str:
        raise NotImplementedError

    def close(self):
        """Release parent-owned resources once the workers have exited."""


class PipeChannel(IPCChannel):
    def __init__(self, num_procs, context=None):
        ctx = context or mp.get_context()
        self.pipes = [ctx.Pipe(duplex=False) for _ in range(num_procs)]
        # A pipe can have concurrent writers, but each must write a complete frame.
        self.write_locks = [ctx.Lock() for _ in self.pipes]

    def send(self, src, dst, data):
        with self.write_locks[dst]:
            self.pipes[dst][1].send(Message(src, dst, data, time.time()))
        return True

    def recv(self, dst):
        reader = self.pipes[dst][0]
        return reader.recv() if reader.poll() else None

    def close(self):
        for reader, writer in self.pipes:
            reader.close()
            writer.close()

    def type_name(self):
        return "Pipe"


class QueueChannel(IPCChannel):
    def __init__(self, num_procs, context=None):
        ctx = context or mp.get_context()
        self.queues = [ctx.Queue() for _ in range(num_procs)]

    def send(self, src, dst, data):
        self.queues[dst].put(Message(src, dst, data, time.time()))
        return True

    def recv(self, dst):
        try:
            return self.queues[dst].get_nowait()
        except Empty:
            return None

    def close(self):
        for inbox in self.queues:
            inbox.close()

    def type_name(self):
        return "MsgQueue"


class _SharedInbox:
    capacity = 64
    message_bytes = 1024

    def __init__(self, context):
        self.data = context.RawArray("B", self.capacity * self.message_bytes)
        self.lengths = context.RawArray("i", self.capacity)
        self.sources = context.RawArray("i", self.capacity)
        self.timestamps = context.RawArray("d", self.capacity)
        self.head = context.RawValue("i", 0)
        self.count = context.RawValue("i", 0)
        self.lock = context.Lock()

    def put(self, src, payload):
        with self.lock:
            if self.count.value == self.capacity:
                return False

            slot = (self.head.value + self.count.value) % self.capacity
            offset = slot * self.message_bytes
            self.data[offset:offset + len(payload)] = payload
            self.lengths[slot] = len(payload)
            self.sources[slot] = src
            self.timestamps[slot] = time.time()
            self.count.value += 1
        return True

    def get(self, dst):
        with self.lock:
            if not self.count.value:
                return None

            slot = self.head.value
            offset = slot * self.message_bytes
            payload = bytes(self.data[offset:offset + self.lengths[slot]])
            message = Message(self.sources[slot], dst, payload.decode(), self.timestamps[slot])
            self.head.value = (slot + 1) % self.capacity
            self.count.value -= 1
            return message


class SharedMemoryChannel(IPCChannel):
    def __init__(self, num_procs, context=None):
        ctx = context or mp.get_context()
        self.inboxes = [_SharedInbox(ctx) for _ in range(num_procs)]

    def send(self, src, dst, data):
        payload = data.encode()
        if len(payload) > _SharedInbox.message_bytes:
            raise ValueError(f"Shared memory messages are limited to {_SharedInbox.message_bytes} bytes")
        return self.inboxes[dst].put(src, payload)

    def recv(self, dst):
        return self.inboxes[dst].get(dst)

    def type_name(self):
        return "SharedMem"
