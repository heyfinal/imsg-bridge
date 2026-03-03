"""GTK <-> asyncio thread bridge.

Runs an asyncio event loop in a background daemon thread, providing
safe cross-thread communication between GTK and async code.
"""

import asyncio
import threading

from gi.repository import GLib


class AsyncBridge:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def stop(self):
        if self._loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop)
            try:
                future.result(timeout=5)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)

    async def _cleanup(self):
        tasks = [t for t in asyncio.all_tasks(self._loop) if t is not asyncio.current_task()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def run_coroutine(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    @staticmethod
    def call_in_gtk(callback, *args):
        GLib.idle_add(callback, *args)
