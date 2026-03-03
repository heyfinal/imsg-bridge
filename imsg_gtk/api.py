"""Async client for the iMessage FastAPI bridge."""

import asyncio
import logging

import aiohttp

log = logging.getLogger(__name__)


class BridgeClient:
    def __init__(self, host: str, port: int, token: str):
        self.host = host
        self.port = port
        self.token = token
        self._session: aiohttp.ClientSession | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers, timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def get_chats(self) -> list[dict]:
        session = await self._get_session()
        async with session.get(f"{self.base_url}/chats") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_history(self, chat_id: str, limit: int = 50) -> list[dict]:
        session = await self._get_session()
        async with session.get(
            f"{self.base_url}/history/{chat_id}", params={"limit": limit}
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_avatar(self, identifier: str) -> bytes | None:
        session = await self._get_session()
        async with session.get(f"{self.base_url}/avatar", params={"identifier": identifier}) as resp:
            if resp.status == 404:
                return None
            resp.raise_for_status()
            return await resp.read()

    async def send_message(self, to: str, text: str) -> dict:
        session = await self._get_session()
        async with session.post(
            f"{self.base_url}/send", json={"to": to, "text": text}
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def health(self) -> dict:
        session = await self._get_session()
        async with session.get(f"{self.base_url}/health") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def connect_ws(self, on_message) -> None:
        backoff = 1
        max_backoff = 30
        while True:
            try:
                session = await self._get_session()
                ws_url = f"ws://{self.host}:{self.port}/ws?token={self.token}"
                async with session.ws_connect(ws_url) as ws:
                    backoff = 1
                    log.info("WebSocket connected to %s", ws_url)
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            on_message(msg.json())
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            log.error("WebSocket error: %s", ws.exception())
                            break
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.CLOSED,
                        ):
                            break
            except asyncio.CancelledError:
                log.info("WebSocket connection cancelled")
                return
            except Exception as exc:
                log.warning("WebSocket disconnected: %s, reconnecting in %ds", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
