"""Local HTTP and WebSocket access to the IPC Nexus simulation."""

import asyncio
import logging
import multiprocessing as mp
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, Field

from diagnostics import LockAcquired, LockWait
from simulation import (
    CHANNELS, ProcessMonitor, Simulation, SimulationConfigurationError, SimulationRunningError,
    WorkerExited, event_text, process_record,
)


LOG = logging.getLogger(__name__)
WEB = Path(__file__).resolve().parent / "web"
IPCMethod = Literal["pipe", "queue", "shared_memory"]


class StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processes: int = Field(strict=True, ge=1, le=20)
    method: IPCMethod


class DeadlockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    armed: bool = Field(strict=True)


class Status(BaseModel):
    running: bool
    processes: int
    method: IPCMethod | None
    deadlock_armed: bool
    available_methods: list[IPCMethod]


class EventHub:
    """Fan out on the event loop without awaiting client I/O."""

    def __init__(self):
        self.clients = set()
        self.snapshots = {}

    def subscribe(self):
        queue = asyncio.Queue(maxsize=256)
        for event in self.snapshots.values():
            queue.put_nowait(event)
        self.clients.add(queue)
        return queue

    def publish(self, event):
        kind = event["type"]
        if kind == "deadlock":
            self.snapshots[kind] = {"type": kind, "data": {**event["data"], "resource": None}}
        elif kind in {"simulation_status", "process_metrics", "performance"}:
            self.snapshots[kind] = event

        for queue in tuple(self.clients):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)


class WebSimulation:
    """Accessed on one executor thread, with a thread-safe event publisher."""

    def __init__(self, publish):
        self.simulation = Simulation()
        self.publish = publish
        self.armed = False
        self.monitor = None
        self.next_sample = 0.0
        self.last_deadlock = None

    def status(self):
        return dict(
            running=self.simulation.running, processes=len(self.simulation.processes),
            method=self.simulation.method, deadlock_armed=self.armed, available_methods=list(CHANNELS),
        )

    def emit(self, event_type, **data):
        self.publish({"type": event_type, "data": data})

    def snapshots(self, workers):
        self.emit("simulation_status", **self.status())
        self.emit("process_metrics", processes=workers)
        self.emit("performance", **self.simulation.performance())
        self.emit_deadlock()

    def start(self, count, method):
        self.simulation.start(count, method, self.armed)
        workers = self.simulation.worker_snapshot()
        self.monitor = ProcessMonitor()
        self.next_sample = time.monotonic()
        self.snapshots([process_record(*worker) for worker in workers])
        for index, pid, _ in workers:
            self.emit("log", kind="process_started",
                      message=f"[P{index}] Spawned OS process {pid}.", index=index, pid=pid)
        return self.status()

    def stop(self):
        if self.simulation.running:
            self.consume(self.simulation.poll_events())
            events = self.simulation.stop()
            self.monitor = None
            self.armed = False
            self.consume(events)
            self.snapshots([])
        return self.status()

    def set_deadlock(self, armed):
        if self.simulation.running:
            raise SimulationRunningError("Stop the simulation before changing deadlock mode.")
        self.armed = armed
        self.emit("simulation_status", **self.status())
        self.emit_deadlock()
        return self.status()

    def poll(self):
        if self.monitor is None:
            return

        self.consume(self.simulation.poll_events())
        if time.monotonic() >= self.next_sample:
            self.emit("process_metrics", processes=self.monitor.sample(self.simulation.worker_snapshot()))
            self.emit("performance", **self.simulation.performance())
            self.next_sample = time.monotonic() + 1

    def consume(self, events):
        for event in events:
            if isinstance(event, tuple):
                message, latency = event
                self.emit("message_delivered", src=message.src, dst=message.dst,
                          message=message.data, timestamp=message.timestamp, latency_ms=latency * 1000)
            elif isinstance(event, WorkerExited):
                self.emit("log", kind="process_exited", message=event_text(event),
                          index=event.index, pid=event.pid, exit_code=event.exit_code)
            else:
                self.emit("log", kind="worker", message=event_text(event))

            if isinstance(event, LockAcquired):
                self.emit_deadlock(dict(action="acquired", index=event.index, lock=event.lock, held=None))
            elif isinstance(event, LockWait):
                self.emit_deadlock(dict(action="waiting", index=event.index, lock=event.requested, held=event.held))
        if self.simulation.running:
            self.emit_deadlock(only_if_changed=True)

    def emit_deadlock(self, resource=None, *, only_if_changed=False):
        diagnostics = self.simulation.diagnostics
        locks = {}
        if diagnostics is not None:
            for lock in ("A", "B"):
                locks[lock] = dict(
                    owner=diagnostics.owners.get(lock),
                    waiters=sorted(event.index for event in diagnostics.waits.values() if event.requested == lock),
                )
        snapshot = dict(
            armed=self.armed, detected=bool(diagnostics and diagnostics.blocked),
            blocked=diagnostics.blocked if diagnostics else [],
            alert=(diagnostics.alert or None) if diagnostics else None,
            locks=locks,
        )
        if only_if_changed and snapshot == self.last_deadlock:
            return
        self.last_deadlock = snapshot
        self.emit("deadlock", **snapshot, resource=resource)


@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ipc-nexus")
    hub = EventHub()

    def publish(event):
        loop.call_soon_threadsafe(hub.publish, event)

    service = WebSimulation(publish)
    app.state.hub = hub
    app.state.service = service
    app.state.run = partial(loop.run_in_executor, executor)

    async def poll():
        while True:
            await asyncio.sleep(0.1)
            try:
                await app.state.run(service.poll)
            except Exception:
                LOG.exception("Could not poll simulation events")

    await app.state.run(service.snapshots, [])
    task = asyncio.create_task(poll())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        try:
            await app.state.run(service.stop)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="IPC Nexus", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


def same_origin(connection):
    origin = connection.headers.get("origin")
    scheme = {"ws": "http", "wss": "https"}.get(connection.url.scheme, connection.url.scheme)
    return origin is None or origin == f"{scheme}://{connection.headers.get('host')}"


async def require_same_origin(request: Request):
    if not same_origin(request):
        raise HTTPException(403, "Use the dashboard's own origin.")


async def command(request, name, *args):
    try:
        return await request.app.state.run(getattr(request.app.state.service, name), *args)
    except SimulationConfigurationError as error:
        raise HTTPException(422, str(error)) from error
    except SimulationRunningError as error:
        raise HTTPException(409, str(error)) from error
    except Exception as error:
        LOG.exception("Simulation command %s failed", name)
        raise HTTPException(500, "Simulation command failed. See the server log.") from error


@app.get("/api/status", response_model=Status)
async def status(request: Request):
    return await command(request, "status")


@app.post("/api/start", response_model=Status, dependencies=[Depends(require_same_origin)])
async def start(body: StartRequest, request: Request):
    return await command(request, "start", body.processes, body.method)


@app.post("/api/stop", response_model=Status, dependencies=[Depends(require_same_origin)])
async def stop(request: Request):
    return await command(request, "stop")


@app.post("/api/deadlock", response_model=Status, dependencies=[Depends(require_same_origin)])
async def deadlock(body: DeadlockRequest, request: Request):
    return await command(request, "set_deadlock", body.armed)


async def send_events(websocket, queue):
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            await asyncio.wait_for(websocket.send_json(event), timeout=5)
    except TimeoutError:
        pass
    except WebSocketDisconnect:
        return

    with suppress(TimeoutError, WebSocketDisconnect):
        await asyncio.wait_for(websocket.close(code=1013), timeout=1)


async def watch_disconnect(websocket):
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass


@app.websocket("/ws")
async def websocket_events(websocket: WebSocket):
    if not same_origin(websocket):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    hub = websocket.app.state.hub
    queue = hub.subscribe()
    sender = asyncio.create_task(send_events(websocket, queue))
    receiver = asyncio.create_task(watch_disconnect(websocket))
    try:
        _, pending = await asyncio.wait((sender, receiver), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)
    finally:
        hub.clients.discard(queue)
        sender.cancel()
        receiver.cancel()
        await asyncio.gather(sender, receiver, return_exceptions=True)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    mp.freeze_support()
    uvicorn.run(app, host="127.0.0.1", port=8000, workers=1, ws="websockets-sansio")
