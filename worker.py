"""Entry point for the simulator's multiprocessing workers."""

import random
import time
from multiprocessing.connection import wait

from diagnostics import LockWait


def process_worker(index, num_procs, channel, stop_reader, log_queue, deadlock=None):
    def log(message):
        log_queue.put(f"[P{index}] {message}")

    log("Started.")
    if deadlock is not None and index in (2, 3):
        locks, both_holding = deadlock
        own = index - 2
        other = 1 - own
        first, second = "AB"[own], "AB"[other]

        with locks[own]:
            log(f"Acquired Lock {first}.")
            both_holding.wait()
            if not locks[other].acquire(block=False):
                log_queue.put(LockWait(index, first, second))
                locks[other].acquire()
            locks[other].release()

    pending = None
    next_send = time.monotonic() + random.uniform(0.5, 2.0)
    try:
        while not wait([stop_reader], timeout=0.01):
            message = channel.recv(index)
            if message is not None:
                log_queue.put((message, time.time() - message.timestamp))

            if pending is None and num_procs > 1 and time.monotonic() >= next_send:
                dst = (index + random.randrange(1, num_procs)) % num_procs
                pending = (dst, f"Hello from P{index}")
            if pending is not None and channel.send(index, *pending):
                pending = None
                next_send = time.monotonic() + random.uniform(0.5, 2.0)

    except (BrokenPipeError, EOFError):
        log("Channel closed. Exiting.")
    finally:
        log("Stopping.")
