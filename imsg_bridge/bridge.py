import asyncio
import getpass
import hmac
import json
import logging
import os
import re
import signal
import subprocess
import sys
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("imessage-bridge")

IMSG = "/opt/homebrew/bin/imsg"
STATE_DIR = Path.home() / ".imessage-bridge"
STATE_FILE = STATE_DIR / "state.json"


# --- Auth ---

def _load_bearer_token() -> str:
    result = subprocess.run(
        ["security", "find-generic-password", "-a", getpass.getuser(), "-s", "imessage-bridge", "-w"],
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise RuntimeError("Failed to load bearer token from Keychain")
    return token


@lru_cache(maxsize=1)
def get_bearer_token() -> str:
    # Allow explicit override for testing and containerized runs.
    token = os.getenv("IMSG_BRIDGE_TOKEN", "").strip()
    if token:
        return token
    return _load_bearer_token()


@lru_cache(maxsize=1)
def get_imsg_version() -> str:
    try:
        result = subprocess.run(
            [IMSG, "--version"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except Exception:
        return "unknown"

    text = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not text:
        return "unknown"

    match = re.search(r"\d+\.\d+\.\d+(?:[-+._a-zA-Z0-9]*)?", text)
    return match.group(0) if match else text


async def verify_token(request: Request) -> None:
    try:
        expected_token = get_bearer_token()
    except RuntimeError as exc:
        logger.error("%s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Server token unavailable")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token")

    presented = auth[7:]
    if not hmac.compare_digest(presented, expected_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing token")


# --- Pydantic Models ---

class Chat(BaseModel):
    id: int
    identifier: str
    name: str
    service: str
    last_message_at: str


class Message(BaseModel):
    model_config = {"extra": "allow"}
    id: int
    guid: str
    chat_id: int
    text: str | None = None
    sender: str | None = None
    destination_caller_id: str | None = None
    is_from_me: bool = False
    created_at: str = ""
    attachments: list[Any] = []
    reactions: list[Any] = []


class SendRequest(BaseModel):
    to: str
    text: str
    file: str | None = None


# --- State Persistence ---

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Corrupt state.json, resetting: %s", exc)
            STATE_FILE.rename(STATE_FILE.with_suffix(".bad"))
    return {}


def save_state(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, STATE_FILE)


# --- Subprocess helpers ---

async def run_imsg(*args: str, timeout: float = 30.0) -> dict | list:
    proc = await asyncio.create_subprocess_exec(
        IMSG,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise HTTPException(status_code=504, detail="imsg command timed out")

    if proc.returncode != 0:
        err = stderr.decode().strip()
        logger.error("imsg %s failed (rc=%d): %s", args, proc.returncode, err)
        raise HTTPException(status_code=502, detail=f"imsg error: {err}")

    raw = stdout.decode().strip()
    # imsg outputs JSONL (one JSON object per line), not a JSON array
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return []
    if len(lines) == 1:
        return json.loads(lines[0])
    return [json.loads(line) for line in lines]


# --- SubprocessManager for watch ---

class SubprocessManager:
    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None
        self._clients: list[WebSocket] = []
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("SubprocessManager started")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("SubprocessManager stopped")

    def add_client(self, ws: WebSocket) -> None:
        self._clients.append(ws)
        logger.info("WS client connected (%d total)", len(self._clients))

    def remove_client(self, ws: WebSocket) -> None:
        if ws in self._clients:
            self._clients.remove(ws)
        logger.info("WS client disconnected (%d remaining)", len(self._clients))

    async def _broadcast(self, data: str) -> None:
        dead: list[WebSocket] = []
        for ws in self._clients:
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove_client(ws)

    async def _run_loop(self) -> None:
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                await self._run_watch()
                backoff = 1.0
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Watch subprocess crashed, restarting in %.1fs", backoff)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff)
                    return
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, 30.0)

    async def _run_watch(self) -> None:
        cmd = [IMSG, "watch", "--json", "--attachments", "--reactions", "--debounce", "250ms"]
        state = load_state()
        last_rowid = state.get("last_rowid")
        if last_rowid is not None:
            cmd.extend(["--since-rowid", str(last_rowid)])

        logger.info("Starting watch: %s", " ".join(cmd))
        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_task = asyncio.create_task(self._drain_stderr(self._proc.stderr))

        async for line in self._proc.stdout:
            if self._stop_event.is_set():
                break
            text = line.decode().strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("Non-JSON line from watch: %s", text)
                continue

            msg_id = msg.get("id")
            if msg_id is not None:
                save_state({"last_rowid": msg_id})

            await self._broadcast(text)

        rc = await self._proc.wait()
        stderr_out = await stderr_task
        if rc != 0 and not self._stop_event.is_set():
            raise RuntimeError(f"Watch exited with code {rc}: {stderr_out}")

    @staticmethod
    async def _drain_stderr(stream) -> str:
        chunks = []
        async for line in stream:
            text = line.decode().strip()
            if text:
                logger.warning("watch stderr: %s", text)
                chunks.append(text)
        return "\n".join(chunks)


# --- App ---

manager = SubprocessManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    loop = asyncio.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(manager.stop()))
    except (NotImplementedError, RuntimeError):
        logger.debug("Signal handlers unavailable in this runtime")
    await manager.start()
    try:
        yield
    finally:
        await manager.stop()


app = FastAPI(title="iMessage Bridge", version="0.1.0", lifespan=lifespan)


@app.post("/send", dependencies=[Depends(verify_token)])
async def send_message(req: SendRequest) -> JSONResponse:
    args = ["send", "--to", req.to, "--text", req.text, "--json"]
    if req.file:
        args.extend(["--file", req.file])
    result = await run_imsg(*args)
    return JSONResponse(content=result)


@app.get("/chats", dependencies=[Depends(verify_token)])
async def list_chats() -> list[Chat]:
    data = await run_imsg("chats", "--json")
    if isinstance(data, dict):
        data = [data]
    return [Chat(**c) for c in data]


@app.get("/history/{chat_id}", dependencies=[Depends(verify_token)])
async def message_history(chat_id: int, limit: int = Query(default=50, ge=1)) -> list[Message]:
    data = await run_imsg("history", "--chat-id", str(chat_id), "--json")
    if isinstance(data, dict):
        data = [data]
    data = data[:limit]
    return [Message(**m) for m in data]


@app.get("/health", dependencies=[Depends(verify_token)])
async def health_check() -> JSONResponse:
    imsg_version = await asyncio.to_thread(get_imsg_version)
    try:
        await run_imsg("chats", "--json", timeout=5.0)
        return JSONResponse(content={"status": "ok", "imsg_version": imsg_version})
    except HTTPException:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "detail": "imsg probe failed", "imsg_version": imsg_version},
        )


@app.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str | None = Query(default=None),
) -> None:
    try:
        expected_token = get_bearer_token()
    except RuntimeError:
        await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="Server token unavailable")
        return

    auth_header = ws.headers.get("Authorization", "")
    header_token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    effective_token = header_token or token

    if not effective_token or not hmac.compare_digest(effective_token, expected_token):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unauthorized")
        return

    await ws.accept()
    manager.add_client(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.remove_client(ws)
