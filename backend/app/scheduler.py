from __future__ import annotations

import threading
import time
from datetime import datetime

from . import db
import json

from .collector import queue_latest, queue_west_suffixes


class DailyScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="domain-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._tick()
            self._stop.wait(20)

    def _tick(self) -> None:
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        run_key = now.strftime("%Y-%m-%d")
        schedules = db.fetch_all(
            """
            SELECT schedules.* FROM schedules
            JOIN sources ON sources.id = schedules.source_id
            WHERE schedules.enabled = 1 AND sources.enabled = 1
            """
        )
        for schedule in schedules:
            if schedule["run_time"] > current_time:
                continue
            # A schedule saved after today's time is due on the next day. Do not
            # backfill it immediately when the service starts or the form is saved.
            updated_at = schedule.get("updated_at", "")
            if updated_at.startswith(run_key) and updated_at[11:16] > schedule["run_time"]:
                continue
            if not db.claim_schedule(schedule["id"], run_key):
                continue
            try:
                suffixes = json.loads(schedule.get("suffixes_json") or "[]")
                source = db.fetch_one("SELECT adapter FROM sources WHERE id = ?", (schedule["source_id"],)) or {}
                if source.get("adapter") == "west_cn":
                    queue_west_suffixes(schedule["source_id"], suffixes, run_key, "schedule")
                else:
                    queue_latest(schedule["source_id"], "txt", "schedule", suffixes=suffixes)
            except Exception:
                # The next scheduled day will retry after a transient source failure.
                continue


scheduler = DailyScheduler()
