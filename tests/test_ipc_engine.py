import multiprocessing as mp
import time
import unittest

from ipc_engine import PipeChannel, QueueChannel, SharedMemoryChannel


WORKERS = 4
SENDS = 12


def exchange(index, channel, barrier, results):
    barrier.wait(timeout=10)
    received = []
    for sequence in range(SENDS):
        dst = (index + 1 + sequence % (WORKERS - 1)) % WORKERS
        while not channel.send(index, dst, f"{index}:{sequence}"):
            time.sleep(0.001)
        message = channel.recv(index)
        if message is not None:
            received.append(message)

    deadline = time.monotonic() + 8
    while len(received) < SENDS and time.monotonic() < deadline:
        message = channel.recv(index)
        if message is None:
            time.sleep(0.001)
        else:
            received.append(message)
    results.put((index, received))


class TestAddressedChannels(unittest.TestCase):
    def check_concurrent_delivery(self, channel_class):
        ctx = mp.get_context("spawn")
        channel = channel_class(WORKERS, ctx)
        results = ctx.Queue()
        barrier = ctx.Barrier(WORKERS)
        workers = [
            ctx.Process(target=exchange, args=(index, channel, barrier, results))
            for index in range(WORKERS)
        ]
        earliest = time.time()
        try:
            for worker in workers:
                worker.start()
            observed = dict(results.get(timeout=20) for _ in workers)
            for worker in workers:
                worker.join(timeout=5)
                self.assertEqual(worker.exitcode, 0)

            latest = time.time()
            self.assertEqual(set(observed), set(range(WORKERS)))
            for dst, messages in observed.items():
                expected = [
                    (src, f"{src}:{sequence}")
                    for src in range(WORKERS)
                    for sequence in range(SENDS)
                    if (src + 1 + sequence % (WORKERS - 1)) % WORKERS == dst
                ]
                self.assertCountEqual([(msg.src, msg.data) for msg in messages], expected)
                for message in messages:
                    self.assertEqual(message.dst, dst)
                    self.assertLessEqual(earliest, message.timestamp)
                    self.assertLessEqual(message.timestamp, latest)
                self.assertIsNone(channel.recv(dst))
        finally:
            for worker in workers:
                if worker.pid is not None:
                    if worker.is_alive():
                        worker.terminate()
                    worker.join()
                worker.close()
            results.close()
            channel.close()

    def test_pipe_concurrent_delivery(self):
        self.check_concurrent_delivery(PipeChannel)

    def test_queue_concurrent_delivery(self):
        self.check_concurrent_delivery(QueueChannel)

    def test_shared_memory_concurrent_delivery(self):
        self.check_concurrent_delivery(SharedMemoryChannel)

    def test_shared_memory_backpressure_and_wraparound(self):
        channel = SharedMemoryChannel(WORKERS)
        inbox = channel.inboxes[3]
        contents = [f"message {index}\0 with text \u2603" for index in range(inbox.capacity)]
        for data in contents:
            self.assertTrue(channel.send(0, 3, data))

        self.assertFalse(channel.send(1, 3, "retry"))
        self.assertIsNone(channel.recv(2))
        self.assertEqual(channel.recv(3).data, contents[0])
        self.assertTrue(channel.send(1, 3, "retry"))
        self.assertEqual([channel.recv(3).data for _ in contents], contents[1:] + ["retry"])
        with self.assertRaises(ValueError):
            channel.send(0, 3, "x" * (inbox.message_bytes + 1))


if __name__ == "__main__":
    unittest.main()
