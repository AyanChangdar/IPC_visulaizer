import json
import socket
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psutil
import uvicorn
from websockets.sync.client import connect

from web_server import app


class TestWebServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        cls.addClassCleanup(listener.close)
        address, port = listener.getsockname()
        cls.http = f"http://{address}:{port}"
        cls.ws = f"ws://{address}:{port}/ws"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws="websockets-sansio"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()

        def shutdown():
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise AssertionError("Uvicorn did not stop")

        cls.addClassCleanup(shutdown)
        deadline = time.monotonic() + 8
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("Uvicorn did not start")

    def setUp(self):
        def reset():
            self.request("/api/stop", "POST")
            self.request("/api/deadlock", "POST", {"armed": False})

        self.addCleanup(reset)

    def request(self, path, method="GET", body=None, origin=None):
        headers = {"Content-Type": "application/json"} if body is not None else {}
        if origin:
            headers["Origin"] = origin
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(self.http + path, data=payload, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=8)
        except HTTPError as error:
            response = error
        with response:
            output = response.read()
            if response.headers.get_content_type() == "application/json":
                output = json.loads(output)
            return response.status, output

    def receive_until(self, websocket, predicate):
        deadline = time.monotonic() + 10
        events = []
        while time.monotonic() < deadline:
            events.append(json.loads(websocket.recv(timeout=max(0, deadline - time.monotonic()))))
            if predicate(events):
                return events
        self.fail("Timed out waiting for WebSocket events")

    def test_status_static_and_invalid_requests(self):
        code, status = self.request("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(status, {
            "running": False, "processes": 0, "method": None, "deadlock_armed": False,
            "available_methods": ["pipe", "queue", "shared_memory"],
        })
        code, page = self.request("/")
        self.assertEqual(code, 200)
        self.assertIsInstance(page, bytes)
        self.assertTrue(page)
        self.assertEqual(self.request("/index.html"), (200, page))
        for body in ({"processes": 0, "method": "pipe"}, {"processes": 4, "method": "invalid"}):
            self.assertEqual(self.request("/api/start", "POST", body)[0], 422)
        self.assertEqual(self.request("/api/stop", "POST", origin="https://example.com")[0], 403)

    def test_single_simulation_streams_delivery_metrics_and_exit(self):
        with connect(self.ws, origin=self.http, open_timeout=5) as websocket:
            idle = json.loads(websocket.recv(timeout=5))
            self.assertEqual(idle["type"], "simulation_status")
            self.assertFalse(idle["data"]["running"])
            body = {"processes": 4, "method": "pipe"}
            with ThreadPoolExecutor(max_workers=2) as callers:
                starts = list(callers.map(lambda _: self.request("/api/start", "POST", body), range(2)))
            self.assertCountEqual([code for code, _ in starts], [200, 409])
            self.assertTrue(next(status for code, status in starts if code == 200)["running"])
            self.assertEqual(self.request("/api/deadlock", "POST", {"armed": True})[0], 409)

            def streaming(events):
                return (
                    sum(e["data"].get("kind") == "process_started" for e in events) == 4
                    and any(e["type"] == "message_delivered" for e in events)
                    and any(e["type"] == "performance" and e["data"]["delivered"] > 0 for e in events)
                    and any(e["type"] == "process_metrics" and len(e["data"]["processes"]) == 4
                            and all(p["cpu"] is not None for p in e["data"]["processes"]) for e in events)
                )

            events = self.receive_until(websocket, streaming)
            created = {e["data"]["pid"] for e in events if e["data"].get("kind") == "process_started"}
            self.assertTrue(created.issubset({child.pid for child in psutil.Process().children()}))
            delivery = next(e["data"] for e in events if e["type"] == "message_delivered")
            self.assertNotEqual(delivery["src"], delivery["dst"])
            self.assertEqual(delivery["message"], f"Hello from P{delivery['src']}")
            self.assertGreater(delivery["timestamp"], 0)
            metrics = [e["data"]["processes"] for e in events if e["type"] == "process_metrics"][-1]
            self.assertEqual({p["pid"] for p in metrics}, created)
            self.assertEqual(set(metrics[0]), {"index", "pid", "cpu", "memory", "status", "alive"})
            self.assertTrue(all(p["alive"] and p["memory"] > 0 and p["status"] for p in metrics))

            code, stopped = self.request("/api/stop", "POST")
            self.assertEqual(code, 200)
            self.assertFalse(stopped["running"])
            events = self.receive_until(websocket, lambda batch: any(
                e["type"] == "process_metrics" and e["data"]["processes"] == [] for e in batch
            ))
            self.assertEqual({e["data"]["pid"] for e in events if e["data"].get("kind") == "process_exited"}, created)
            self.assertTrue(all(not psutil.pid_exists(pid) for pid in created))

    def test_deadlock_resource_events_and_reset(self):
        code, status = self.request("/api/deadlock", "POST", {"armed": True})
        self.assertEqual(code, 200)
        self.assertTrue(status["deadlock_armed"])
        self.assertEqual(self.request("/api/start", "POST", {"processes": 4, "method": "queue"})[0], 422)
        with connect(self.ws, origin=self.http, open_timeout=5) as websocket:
            self.assertEqual(self.request("/api/start", "POST", {"processes": 4, "method": "shared_memory"})[0], 200)

            def resources(events):
                resource_events = [e["data"] for e in events if e["type"] == "deadlock" and e["data"]["resource"]]
                return len(resource_events) == 4 and any(e["detected"] for e in resource_events)

            events = self.receive_until(websocket, resources)
            deadlock = [e["data"] for e in events if e["type"] == "deadlock"][-1]
            self.assertTrue(deadlock["detected"])
            self.assertEqual(deadlock["blocked"], [2, 3])
            self.assertEqual(deadlock["locks"], {
                "A": {"owner": 2, "waiters": [3]}, "B": {"owner": 3, "waiters": [2]},
            })
            _, status = self.request("/api/stop", "POST")
            self.assertFalse(status["deadlock_armed"])
            events = self.receive_until(websocket, lambda batch: any(
                e["type"] == "deadlock" and not e["data"]["armed"] for e in batch
            ))
            deadlock = events[-1]["data"]
            self.assertFalse(deadlock["detected"])
            self.assertEqual(deadlock["locks"], {})
