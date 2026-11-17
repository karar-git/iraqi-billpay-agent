"""Per-sender serial processing with a short debounce.

People type in bursts: «دز لهادي» then «خمسين الف» two seconds later. Texts from one sender that
arrive within `debounce` seconds of each other are handed over together (the channel joins them
into one message), up to `max_wait` seconds after the first. A button tap is never held back, nor is a
text `ready` says needs nothing after it (a PIN).

One sender's batches never run at the same time, so a tap and a PIN are always handled in the order
they came; different senders run in parallel on a small thread pool.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from channels.whatsapp.graph import Inbound

log = logging.getLogger("whatsapp.dispatch")


class Dispatcher:
    def __init__(self, handle: Callable[[str, list[Inbound]], None], *, debounce: float = 4.0,
                 max_wait: float = 12.0, workers: int = 8, ready: Callable[[Inbound], bool] | None = None):
        self.handle = handle
        self.debounce = debounce
        self.max_wait = max_wait
        self.ready = ready
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wa-sender")
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._queues: dict[str, list[tuple[float, Inbound]]] = {}
        self._running: set[str] = set()
        self._closed = False

    def submit(self, item: Inbound) -> None:
        with self._lock:
            if self._closed:
                return
            self._queues.setdefault(item.sender, []).append((time.monotonic(), item))
            if item.sender in self._running:
                return  # the sender's worker picks it up when its current batch is done
            self._running.add(item.sender)
        self._pool.submit(self._drain, item.sender)

    def _drain(self, sender: str) -> None:
        try:
            while True:
                self._wait_quiet(sender)
                with self._lock:
                    queue = self._queues.get(sender) or []
                    if not queue:
                        self._queues.pop(sender, None)
                        self._running.discard(sender)
                        self._idle.notify_all()
                        return
                    batch = [item for _, item in queue]
                    self._queues[sender] = []
                try:
                    self.handle(sender, batch)
                except Exception:  # one bad batch never stops the sender's later messages
                    log.exception("batch failed for a sender")
        except BaseException:
            with self._lock:
                self._running.discard(sender)
                self._idle.notify_all()
            raise

    def _wait_quiet(self, sender: str) -> None:
        """Hold a batch that ends in a text until the sender stops typing (or max_wait passes)."""
        if self.debounce <= 0:
            return
        started = time.monotonic()
        while True:
            with self._lock:
                queue = self._queues.get(sender) or []
                if not queue or queue[-1][1].kind != "text" or (self.ready and self.ready(queue[-1][1])):
                    return
                last = queue[-1][0]
            now = time.monotonic()
            left = last + self.debounce - now
            if left <= 0 or now - started >= self.max_wait:
                return
            time.sleep(min(left, 0.1))

    def waiting(self, sender: str) -> list[Inbound]:
        """What this sender has sent since the batch now running was taken."""
        with self._lock:
            return [item for _, item in self._queues.get(sender) or []]

    def join(self, timeout: float = 30.0) -> bool:
        """Wait until every queued message was handled (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while self._running or any(self._queues.values()):
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._idle.wait(min(left, 0.5))
        return True

    def close(self, timeout: float = 20.0) -> None:
        self.join(timeout)
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=True, cancel_futures=True)
