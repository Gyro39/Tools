"""
Precision scheduler with countdown display.
Triggers the grab callback at the exact target time.
If target time has passed, fires immediately.
"""
import time
import threading
import logging
from datetime import datetime
from typing import Callable

_slog = logging.getLogger("bw_ticket")


class PrecisionScheduler:
    """Counts down to a target time and fires a callback with sub-second precision."""

    def __init__(self, target_time: str, advance_ms: int = 300,
                 callback: Callable = None):
        self.target_dt = datetime.strptime(target_time, "%Y-%m-%d %H:%M:%S")
        self.target_ts = self.target_dt.timestamp()
        self.advance_s = advance_ms / 1000.0
        self.callback = callback
        self._stop = threading.Event()
        self._triggered = False
        self._result = None

    @property
    def now_ts(self) -> float:
        return time.time()

    @property
    def remaining_s(self) -> float:
        return self.target_ts - self.now_ts

    def set_callback(self, fn: Callable):
        self.callback = fn

    def stop(self):
        self._stop.set()

    def start(self, blocking: bool = True):
        if self.remaining_s < -10:
            _slog.info("Sale time has passed, firing immediately")
            if self.callback:
                try:
                    self._result = self.callback()
                except Exception as e:
                    _slog.error("Callback error: %s" % str(e))
            return self._result

        t = threading.Thread(target=self._run, daemon=True)
        t.start()
        if blocking:
            t.join()
        return self._result

    def _run(self):
        fire_at = self.target_ts - self.advance_s
        last_print = 0

        while not self._stop.is_set():
            remaining = fire_at - self.now_ts
            if remaining <= 0:
                break

            if remaining > 5 and (int(remaining) != last_print):
                last_print = int(remaining)
            elif remaining <= 5:
                pass

            sleep_time = min(0.01, remaining * 0.5) if remaining < 1 else 0.1
            if sleep_time > 0:
                time.sleep(sleep_time)

        _slog.info("FIRE! Actual offset: %.1fms" % ((self.now_ts - fire_at) * 1000))
        self._triggered = True

        if self.callback:
            try:
                self._result = self.callback()
            except Exception as e:
                _slog.error("Callback error: %s" % str(e))
