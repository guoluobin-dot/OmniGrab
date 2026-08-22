"""Bounded, lazy browser-reader pool (R4)."""
from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from typing import Iterator

from .browser_reader import BrowserProfileReader
from .config import CoreConfig


class BrowserPool:
    def __init__(self, config: CoreConfig | None = None) -> None:
        self.config = config or CoreConfig()
        self._slots: queue.LifoQueue[BrowserProfileReader] = queue.LifoQueue(self.config.browser_pool_size)
        self._semaphore = threading.BoundedSemaphore(self.config.max_concurrency)
        self._created = 0
        self._lock = threading.Lock()

    @contextmanager
    def acquire(self) -> Iterator[BrowserProfileReader]:
        self._semaphore.acquire()
        reader = None
        try:
            try: reader = self._slots.get_nowait()
            except queue.Empty:
                with self._lock:
                    self._created += 1
                reader = BrowserProfileReader(headless=self.config.headless, browser_profile_dir=self.config.browser_profile_dir, keep_open=True)
            yield reader
        finally:
            # Kept-open reader instances are returned to a bounded pool, preventing
            # unbounded Chrome launches while preserving the persistent session.
            if reader:
                try: self._slots.put_nowait(reader)
                except queue.Full: reader.close()
            self._semaphore.release()

    def close(self) -> None:
        while True:
            try: self._slots.get_nowait().close()
            except queue.Empty: return
