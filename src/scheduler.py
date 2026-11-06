import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEDULER_CONFIG = PROJECT_ROOT / "data" / "scheduler_config.json"

DEFAULT_STATE = {
    "enabled": False,
    "interval_minutes": 240,
    "last_run_at": None,
    "next_run_at": None,
    "last_status": "idle",
    "last_message": "Auto-fetch is disabled.",
    "last_match_count": 0,
}


def load_scheduler_state(config_path: Path = DEFAULT_SCHEDULER_CONFIG) -> dict:
    state = dict(DEFAULT_STATE)
    if not config_path.exists():
        return state
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state.update(loaded)
    except (OSError, json.JSONDecodeError):
        pass
    state["enabled"] = bool(state.get("enabled", False))
    try:
        state["interval_minutes"] = max(15, min(10080, int(state.get("interval_minutes", 240))))
    except (TypeError, ValueError):
        state["interval_minutes"] = 240
    return state


def save_scheduler_state(state: dict, config_path: Path = DEFAULT_SCHEDULER_CONFIG) -> dict:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_STATE)
    merged.update(state)
    config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return merged


def update_scheduler_settings(
    enabled: bool,
    interval_minutes: int = 240,
    config_path: Path = DEFAULT_SCHEDULER_CONFIG,
) -> dict:
    try:
        normalized_interval = int(interval_minutes)
    except (TypeError, ValueError) as error:
        raise ValueError("Interval must be an integer number of minutes.") from error
    if normalized_interval < 15 or normalized_interval > 10080:
        raise ValueError("Interval must be between 15 minutes and 7 days (10080 minutes).")

    state = load_scheduler_state(config_path)
    state["enabled"] = bool(enabled)
    state["interval_minutes"] = normalized_interval
    now = datetime.now(timezone.utc)
    if state["enabled"]:
        state["next_run_at"] = (now + timedelta(minutes=normalized_interval)).isoformat()
        state["last_message"] = f"Scheduled every {normalized_interval} min."
    else:
        state["next_run_at"] = None
        state["last_message"] = "Auto-fetch is disabled."
    return save_scheduler_state(state, config_path)


def run_scheduled_scan_once(
    scan_fn,
    config_path: Path = DEFAULT_SCHEDULER_CONFIG,
) -> dict:
    state = load_scheduler_state(config_path)
    now = datetime.now(timezone.utc)
    state["last_status"] = "running"
    state["last_message"] = "Running scheduled source scan..."
    save_scheduler_state(state, config_path)

    try:
        result = scan_fn()
        matches = result.get("matches", []) if isinstance(result, dict) else (result or [])
        finished = datetime.now(timezone.utc)
        state = load_scheduler_state(config_path)
        state["last_run_at"] = finished.isoformat()
        state["last_status"] = "success"
        state["last_match_count"] = len(matches)
        state["last_message"] = f"Auto-scan complete: {len(matches)} matches."
        if state.get("enabled"):
            state["next_run_at"] = (
                finished + timedelta(minutes=int(state.get("interval_minutes", 240)))
            ).isoformat()
        return save_scheduler_state(state, config_path)
    except Exception as error:
        finished = datetime.now(timezone.utc)
        state = load_scheduler_state(config_path)
        state["last_run_at"] = finished.isoformat()
        state["last_status"] = "error"
        state["last_message"] = str(error) or "Scheduled scan failed."
        if state.get("enabled"):
            state["next_run_at"] = (
                finished + timedelta(minutes=int(state.get("interval_minutes", 240)))
            ).isoformat()
        return save_scheduler_state(state, config_path)


class AutoScanDaemon:
    """Background thread that triggers scheduled job scans when due."""

    def __init__(self, scan_fn, config_path: Path = DEFAULT_SCHEDULER_CONFIG, poll_seconds: float = 15.0):
        self.scan_fn = scan_fn
        self.config_path = config_path
        self.poll_seconds = poll_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name="AutoScanDaemon", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                state = load_scheduler_state(self.config_path)
                if state.get("enabled"):
                    next_run_raw = state.get("next_run_at")
                    now = datetime.now(timezone.utc)
                    due = False
                    if not next_run_raw:
                        due = True
                    else:
                        try:
                            next_run = datetime.fromisoformat(str(next_run_raw).replace("Z", "+00:00"))
                            if next_run.tzinfo is None:
                                next_run = next_run.replace(tzinfo=timezone.utc)
                            due = now >= next_run
                        except ValueError:
                            due = True
                    if due and self._lock.acquire(blocking=False):
                        try:
                            run_scheduled_scan_once(self.scan_fn, self.config_path)
                        finally:
                            self._lock.release()
            except Exception:
                pass
            self._stop_event.wait(self.poll_seconds)

