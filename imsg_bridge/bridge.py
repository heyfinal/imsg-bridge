import asyncio
import collections
import getpass
import hmac
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
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
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("imessage-bridge")

IMSG = os.getenv("IMSG_PATH") or shutil.which("imsg") or "/opt/homebrew/bin/imsg"
STATE_DIR = Path.home() / ".imessage-bridge"
STATE_FILE = STATE_DIR / "state.json"
ADDRESSBOOK_DB = Path.home() / "Library" / "Application Support" / "AddressBook" / "AddressBook-v22.abcddb"
ADDRESSBOOK_EXTERNAL_DATA = (
    Path.home() / "Library" / "Application Support" / "AddressBook" / ".AddressBook-v22_SUPPORT" / "_EXTERNAL_DATA"
)


def _normalize_phone(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def _phone_lookup_keys(value: str) -> list[str]:
    digits = _normalize_phone(value)
    if not digits:
        return []

    keys = [f"phone:{digits}"]
    if len(digits) > 10:
        keys.append(f"phone:{digits[-10:]}")
    if len(digits) >= 7:
        keys.append(f"phone7:{digits[-7:]}")
    if len(digits) >= 4:
        keys.append(f"phone4:{digits[-4:]}")
    return keys


def _decode_contact_image_blob(blob: bytes | None) -> bytes | None:
    if not blob:
        return None

    # AddressBook can store an external-image pointer blob:
    # 0x02 + ASCII UUID + NUL
    if blob.startswith(b"\x02"):
        ref_bytes = blob[1:].split(b"\x00", maxsplit=1)[0]
        try:
            ref = ref_bytes.decode("utf-8").strip()
        except UnicodeDecodeError:
            ref = ""

        if not ref:
            return None

        external_path = ADDRESSBOOK_EXTERNAL_DATA / ref
        try:
            return external_path.read_bytes() if external_path.exists() else None
        except OSError:
            return None

    return bytes(blob)


_avatar_cache: dict[str, bytes] = {}
_avatar_cache_time: float = 0.0
_AVATAR_TTL = 300.0  # 5 minutes

_name_cache: dict[str, str] = {}
_name_cache_time: float = 0.0
_NAME_TTL = 300.0


def _build_contact_index() -> tuple[dict[str, bytes], dict[str, str]]:
    avatar_index: dict[str, bytes] = {}
    name_index: dict[str, str] = {}
    if not ADDRESSBOOK_DB.exists():
        return avatar_index, name_index

    try:
        conn = sqlite3.connect(f"file:{ADDRESSBOOK_DB}?mode=ro", uri=True)
    except sqlite3.Error:
        return avatar_index, name_index

    try:
        phone_rows = conn.execute(
            """
            SELECT
              p.ZFULLNUMBER,
              p.ZLASTFOURDIGITS,
              COALESCE(r.ZTHUMBNAILIMAGEDATA, r.ZIMAGEDATA) AS IMAGE_BLOB,
              r.ZFIRSTNAME,
              r.ZLASTNAME,
              r.ZORGANIZATION
            FROM ZABCDPHONENUMBER p
            JOIN ZABCDRECORD r ON (p.ZOWNER = r.Z_PK OR p.Z22_OWNER = r.Z_PK)
            """
        )
        for full_number, last_four, image_blob, first, last, org in phone_rows:
            display_name = _format_display_name(first, last, org)
            image = _decode_contact_image_blob(image_blob)

            for key in _phone_lookup_keys(full_number or ""):
                if image:
                    avatar_index.setdefault(key, image)
                if display_name:
                    name_index.setdefault(key, display_name)

            if last_four:
                digits = _normalize_phone(last_four)
                if digits:
                    k = f"phone4:{digits[-4:]}"
                    if image:
                        avatar_index.setdefault(k, image)
                    if display_name:
                        name_index.setdefault(k, display_name)

        email_rows = conn.execute(
            """
            SELECT
              LOWER(e.ZADDRESS),
              COALESCE(r.ZTHUMBNAILIMAGEDATA, r.ZIMAGEDATA) AS IMAGE_BLOB,
              r.ZFIRSTNAME,
              r.ZLASTNAME,
              r.ZORGANIZATION
            FROM ZABCDEMAILADDRESS e
            JOIN ZABCDRECORD r ON (e.ZOWNER = r.Z_PK OR e.Z22_OWNER = r.Z_PK)
            """
        )
        for email, image_blob, first, last, org in email_rows:
            if not email:
                continue
            display_name = _format_display_name(first, last, org)
            image = _decode_contact_image_blob(image_blob)
            k = f"email:{email}"
            if image:
                avatar_index.setdefault(k, image)
            if display_name:
                name_index.setdefault(k, display_name)
    except sqlite3.Error:
        logger.debug("Failed to query AddressBook contact index", exc_info=True)
    finally:
        conn.close()

    return avatar_index, name_index


def _format_display_name(first: str | None, last: str | None, org: str | None) -> str:
    if first and last:
        return f"{first} {last}"
    if first:
        return first
    if last:
        return last
    if org:
        return org
    return ""


def _contact_avatar_index() -> dict[str, bytes]:
    global _avatar_cache, _avatar_cache_time, _name_cache, _name_cache_time
    now = time.monotonic()
    if now - _avatar_cache_time > _AVATAR_TTL:
        _avatar_cache, _name_cache = _build_contact_index()
        _avatar_cache_time = now
        _name_cache_time = now
    return _avatar_cache


def _contact_name_index() -> dict[str, str]:
    global _avatar_cache, _avatar_cache_time, _name_cache, _name_cache_time
    now = time.monotonic()
    if now - _name_cache_time > _NAME_TTL:
        _avatar_cache, _name_cache = _build_contact_index()
        _avatar_cache_time = now
        _name_cache_time = now
    return _name_cache


def get_contact_avatar(identifier: str) -> bytes | None:
    ident = identifier.strip()
    if not ident:
        return None

    index = _contact_avatar_index()
    lowered = ident.lower()

    if "@" in lowered:
        return index.get(f"email:{lowered}")

    for key in _phone_lookup_keys(lowered):
        image = index.get(key)
        if image:
            return image

    return None


def get_contact_name(identifier: str) -> str | None:
    ident = identifier.strip()
    if not ident:
        return None

    index = _contact_name_index()
    lowered = ident.lower()

    if "@" in lowered:
        return index.get(f"email:{lowered}")

    for key in _phone_lookup_keys(lowered):
        name = index.get(key)
        if name:
            return name

    return None


def _avatar_media_type(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if image_bytes.startswith(b"II*\x00") or image_bytes.startswith(b"MM\x00*"):
        return "image/tiff"
    return "application/octet-stream"


# --- Auth ---

def _load_bearer_token() -> str:
    env_token = os.environ.get("IMSG_BRIDGE_TOKEN", "").strip()
    if env_token:
        return env_token

    result = subprocess.run(
        ["security", "find-generic-password", "-a", getpass.getuser(), "-s", "imessage-bridge", "-w"],
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise RuntimeError(
            "Bearer token not found in Keychain. Run ./setup.sh to generate and store it."
        )
    return token


@lru_cache(maxsize=1)
def get_bearer_token() -> str:
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
        logger.error("Bearer token unavailable: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable",
        )

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
    attachments: list[Any] = Field(default_factory=list)
    reactions: list[Any] = Field(default_factory=list)


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
        partial_stderr = b""
        if proc.stderr:
            try:
                partial_stderr = await asyncio.wait_for(proc.stderr.read(4096), timeout=0.1)
            except (asyncio.TimeoutError, Exception):
                pass
        proc.kill()
        await proc.wait()
        detail = "imsg command timed out"
        if partial_stderr:
            detail += f": {partial_stderr.decode(errors='replace').strip()}"
        raise HTTPException(status_code=504, detail=detail)

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


# --- Rate Limiting ---

class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = asyncio.Lock()

    async def check(self) -> tuple[bool, int, int]:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            remaining = max(self.max_requests - len(self._timestamps), 0)
            if len(self._timestamps) < self.max_requests:
                self._timestamps.append(now)
                return True, remaining - 1, 0
            retry_after = int(self._timestamps[0] + self.window_seconds - now) + 1
            return False, 0, retry_after


send_limiter = SlidingWindowLimiter(max_requests=20, window_seconds=60.0)


async def rate_limit_send(request: Request) -> None:
    allowed, remaining, retry_after = await send_limiter.check()
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


# --- App ---

manager = SubprocessManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Fail fast (and with a clear log message) if the server isn't configured.
    try:
        get_bearer_token()
    except RuntimeError as exc:
        logger.error("Bridge startup failed: bearer token unavailable: %s", exc)
        raise

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


@app.get("/ping")
async def ping() -> JSONResponse:
    return JSONResponse(content={"status": "ok"})


@app.post("/send", dependencies=[Depends(verify_token), Depends(rate_limit_send)])
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


@app.get("/avatar", dependencies=[Depends(verify_token)])
async def contact_avatar(identifier: str = Query(min_length=1)) -> Response:
    image_bytes = await asyncio.to_thread(get_contact_avatar, identifier)
    if not image_bytes:
        raise HTTPException(status_code=404, detail="Avatar not found")
    return Response(content=image_bytes, media_type=_avatar_media_type(image_bytes))


@app.get("/contact-name", dependencies=[Depends(verify_token)])
async def contact_name(identifier: str = Query(min_length=1)) -> JSONResponse:
    name = await asyncio.to_thread(get_contact_name, identifier)
    if not name:
        raise HTTPException(status_code=404, detail="Contact not found")
    return JSONResponse(content={"identifier": identifier, "name": name})


@app.get("/history/{chat_id}", dependencies=[Depends(verify_token)])
async def message_history(chat_id: int, limit: int = Query(default=50, ge=1)) -> list[Message]:
    data = await run_imsg("history", "--chat-id", str(chat_id), "--limit", str(limit), "--json")
    if isinstance(data, dict):
        data = [data]

    # Normalize order for clients: oldest -> newest so latest stays at bottom in chat UIs.
    def _sort_key(msg: dict) -> tuple[int, str]:
        msg_id = msg.get("id")
        try:
            numeric_id = int(msg_id)
        except (TypeError, ValueError):
            numeric_id = -1
        return (numeric_id, str(msg.get("created_at", "")))

    data = sorted(data, key=_sort_key)
    data = data[-limit:]
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
        await ws.close(code=status.WS_1011_INTERNAL_ERROR, reason="Service unavailable")
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
