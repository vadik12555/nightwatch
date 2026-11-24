#!/usr/bin/env python3
"""NightWatch agent — collects server health data and ships it to the NightWatch backend.

Every `interval` seconds the agent:
  1. reads system metrics with psutil (CPU, RAM, disk, network rate, load, uptime);
  2. asks systemd for the state of the configured services (`systemctl is-active`);
  3. reads *new* log lines from journald and plain log files (position is persisted);
  4. POSTs a JSON report to /api/v1/ingest/ with `Authorization: Token <token>`.

Network failures are retried with exponential backoff + jitter; reports that still
fail are kept in a bounded in-memory buffer and re-sent, oldest first, next cycle.

Runs on Python 3.9+ (stock python3 of current LTS distributions).

Usage:
    nightwatch_agent.py --config /etc/nightwatch/agent.yaml
    nightwatch_agent.py --config agent.yaml --once --dry-run   # print one report
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import random
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil
import requests
import yaml

__version__ = "0.1.0"

log = logging.getLogger("nightwatch.agent")

DEFAULT_CONFIG_PATH = "/etc/nightwatch/agent.yaml"
SERVICE_STATES = {"active", "inactive", "failed", "activating", "deactivating"}
# journald PRIORITY (syslog severity) -> NightWatch level
JOURNAL_PRIORITY_LEVELS = {
    0: "critical",  # emerg
    1: "critical",  # alert
    2: "critical",  # crit
    3: "error",
    4: "warning",
    5: "info",  # notice
    6: "info",
    7: "debug",
}
LEVEL_PATTERNS = [
    ("critical", re.compile(r"\b(crit(ical)?|fatal|emerg(ency)?|panic|alert)\b", re.I)),
    ("error", re.compile(r"\b(err(or)?|fail(ed|ure)?|exception|traceback)\b", re.I)),
    ("warning", re.compile(r"\b(warn(ing)?)\b", re.I)),
    ("debug", re.compile(r"\b(debug|trace)\b", re.I)),
]
MAX_MESSAGE_LENGTH = 8_000


# --- Configuration ---------------------------------------------------------------


class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_url: str
    token: str
    interval: int = 15
    timeout: float = 10.0
    max_retries: int = 3
    verify_tls: bool = True
    buffer_size: int = 120
    disk_path: str = "/"
    services: list[str] = field(default_factory=list)
    journal_enabled: bool = True
    journal_units: list[str] = field(default_factory=list)
    journal_priority: str = "info"
    log_files: list[str] = field(default_factory=list)
    max_log_lines: int = 200
    state_file: str = "/var/lib/nightwatch-agent/state.json"
    log_level: str = "INFO"

    @property
    def ingest_url(self) -> str:
        return self.api_url.rstrip("/") + "/api/v1/ingest/"


def load_config(path: str | os.PathLike[str]) -> Config:
    """Load YAML config. NIGHTWATCH_API_URL / NIGHTWATCH_TOKEN env vars override the file."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("Config root must be a mapping.")

    journal = raw.get("journal") or {}
    logs = raw.get("logs") or {}
    config = Config(
        api_url=os.environ.get("NIGHTWATCH_API_URL") or raw.get("api_url", ""),
        token=os.environ.get("NIGHTWATCH_TOKEN") or raw.get("token", ""),
        interval=int(raw.get("interval", 15)),
        timeout=float(raw.get("timeout", 10)),
        max_retries=int(raw.get("max_retries", 3)),
        verify_tls=bool(raw.get("verify_tls", True)),
        buffer_size=int(raw.get("buffer_size", 120)),
        disk_path=str(raw.get("disk_path", "/")),
        services=[str(s) for s in raw.get("services") or []],
        journal_enabled=bool(journal.get("enabled", True)),
        journal_units=[str(u) for u in journal.get("units") or []],
        journal_priority=str(journal.get("priority", "info")),
        log_files=[str(f) for f in logs.get("files") or []],
        max_log_lines=int(logs.get("max_lines_per_cycle", 200)),
        state_file=str(raw.get("state_file", Config.state_file)),
        log_level=str(raw.get("log_level", "INFO")).upper(),
    )

    if not config.api_url.startswith(("http://", "https://")):
        raise ConfigError("api_url must start with http:// or https://")
    if not config.token:
        raise ConfigError("token is required (or set NIGHTWATCH_TOKEN).")
    if config.interval < 5:
        raise ConfigError("interval must be at least 5 seconds.")
    return config


# --- Persistent state (log positions survive restarts) --------------------------------


class StateStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable state file %s: %s", self.path, exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data), encoding="utf-8")
            os.replace(tmp, self.path)  # atomic: never leaves a half-written file
        except OSError as exc:
            log.warning("Cannot save state to %s: %s", self.path, exc)


# --- Metrics ---------------------------------------------------------------------------


class MetricsCollector:
    """Reads system metrics. Network counters are cumulative, so rates need the previous sample."""

    def __init__(self, disk_path: str = "/") -> None:
        self.disk_path = disk_path
        self._prev_net: tuple[float, int, int] | None = None
        psutil.cpu_percent(interval=None)  # prime: the first call always returns 0.0

    def collect(self) -> dict[str, Any]:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(self.disk_path)
        try:
            load_1, load_5, load_15 = psutil.getloadavg()
        except (AttributeError, OSError):  # pragma: no cover - platform without loadavg
            load_1 = load_5 = load_15 = 0.0
        sent_bps, recv_bps = self._network_rates()

        return {
            "cpu_percent": round(psutil.cpu_percent(interval=None), 2),
            "memory_percent": round(memory.percent, 2),
            "memory_used": int(memory.total - memory.available),
            "memory_total": int(memory.total),
            "disk_percent": round(disk.percent, 2),
            "disk_used": int(disk.used),
            "disk_total": int(disk.total),
            "net_sent_bps": round(sent_bps, 2),
            "net_recv_bps": round(recv_bps, 2),
            "load_1": round(load_1, 2),
            "load_5": round(load_5, 2),
            "load_15": round(load_15, 2),
            "uptime_seconds": max(0, int(time.time() - psutil.boot_time())),
        }

    def _network_rates(self) -> tuple[float, float]:
        counters = psutil.net_io_counters()
        now = time.monotonic()
        current = (now, counters.bytes_sent, counters.bytes_recv)
        previous, self._prev_net = self._prev_net, current
        if previous is None:
            return 0.0, 0.0
        elapsed = now - previous[0]
        if elapsed <= 0:
            return 0.0, 0.0
        # max(0, …): counters reset when an interface goes down or the host reboots.
        sent = max(0, counters.bytes_sent - previous[1]) / elapsed
        recv = max(0, counters.bytes_recv - previous[2]) / elapsed
        return sent, recv


# --- systemd services -----------------------------------------------------------------


def service_status(name: str) -> str:
    """`systemctl is-active <unit>` prints the state even when it exits non-zero."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"
    state = result.stdout.strip()
    return state if state in SERVICE_STATES else "unknown"


def collect_services(names: list[str]) -> list[dict[str, str]]:
    return [{"name": name, "status": service_status(name)} for name in names]


# --- Logs -------------------------------------------------------------------------------


def detect_level(line: str) -> str:
    for level, pattern in LEVEL_PATTERNS:
        if pattern.search(line):
            return level
    return "info"


def _clip(message: str) -> str:
    message = message.rstrip("\n")
    return message if len(message) <= MAX_MESSAGE_LENGTH else message[:MAX_MESSAGE_LENGTH]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JournalReader:
    """Reads journald entries newer than the cursor saved in the state file."""

    def __init__(self, config: Config, state: StateStore) -> None:
        self.units = config.journal_units
        self.priority = config.journal_priority
        self.max_lines = config.max_log_lines
        self.state = state
        self.available = True

    def read(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        cmd = ["journalctl", "--output=json", "--no-pager", f"--priority={self.priority}"]
        for unit in self.units:
            cmd.append(f"--unit={unit}")
        cursor = self.state.data.get("journal_cursor")
        if cursor:
            cmd.append(f"--after-cursor={cursor}")
        else:
            # First run: start with a small tail instead of the whole history.
            cmd.append("--lines=20")

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, check=False, errors="replace"
            )
        except FileNotFoundError:
            log.warning("journalctl not found — journald collection disabled.")
            self.available = False
            return []
        except subprocess.TimeoutExpired:
            log.warning("journalctl timed out")
            return []
        if result.returncode != 0:
            log.warning("journalctl failed: %s", result.stderr.strip()[:200])
            return []

        entries = []
        for line in result.stdout.splitlines():
            entry = self.parse_entry(line)
            if entry is None:
                continue
            record, entry_cursor = entry
            entries.append(record)
            if entry_cursor:
                self.state.data["journal_cursor"] = entry_cursor
        # The cursor moved past everything read; keep only the newest lines if there were many.
        return entries[-self.max_lines :]

    @staticmethod
    def parse_entry(line: str) -> tuple[dict[str, Any], str | None] | None:
        try:
            raw = json.loads(line)
        except ValueError:
            return None
        message = raw.get("MESSAGE")
        if isinstance(message, list):  # binary-safe form: list of byte values
            message = bytes(message).decode("utf-8", errors="replace")
        if not message:
            return None
        try:
            priority = int(raw.get("PRIORITY", 6))
        except (TypeError, ValueError):
            priority = 6
        try:
            micros = int(raw["__REALTIME_TIMESTAMP"])
            timestamp = datetime.fromtimestamp(micros / 1_000_000, tz=timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError):
            timestamp = _now_iso()
        source = raw.get("SYSLOG_IDENTIFIER") or raw.get("_SYSTEMD_UNIT") or "journal"
        record = {
            "timestamp": timestamp,
            "level": JOURNAL_PRIORITY_LEVELS.get(priority, "info"),
            "source": str(source)[:255],
            "message": _clip(str(message)),
        }
        return record, raw.get("__CURSOR")


class FileTailer:
    """`tail -F` for several files: remembers offset + inode, survives logrotate."""

    INITIAL_LINES = 20
    MAX_READ_BYTES = 1_000_000

    def __init__(self, config: Config, state: StateStore) -> None:
        self.paths = config.log_files
        self.max_lines = config.max_log_lines
        self.state = state

    def read(self) -> list[dict[str, Any]]:
        positions: dict[str, dict[str, int]] = self.state.data.setdefault("files", {})
        entries: list[dict[str, Any]] = []
        for path in self.paths:
            try:
                entries.extend(self._read_file(path, positions))
            except OSError as exc:
                log.warning("Cannot read %s: %s", path, exc)
        return entries[-self.max_lines :]

    def _read_file(self, path: str, positions: dict[str, dict[str, int]]) -> list[dict[str, Any]]:
        stat = os.stat(path)
        known = positions.get(path)
        with open(path, "rb") as fh:
            if known is None:
                # Never seen: show the last few lines, then follow from the end.
                lines = self._tail(fh, stat.st_size)
                offset = stat.st_size
            else:
                rotated = known["inode"] != stat.st_ino or stat.st_size < known["offset"]
                offset = 0 if rotated else known["offset"]
                fh.seek(offset)
                chunk = fh.read(self.MAX_READ_BYTES)
                # Only consume complete lines; a partial last line is read next time.
                end = chunk.rfind(b"\n") + 1
                lines = chunk[:end].decode("utf-8", errors="replace").splitlines()
                offset += end
        positions[path] = {"inode": stat.st_ino, "offset": offset}

        source = os.path.basename(path)
        return [
            {
                "timestamp": _now_iso(),
                "level": detect_level(line),
                "source": source,
                "message": _clip(line),
            }
            for line in lines
            if line.strip()
        ]

    def _tail(self, fh, size: int) -> list[str]:
        fh.seek(max(0, size - 64_000))
        data = fh.read().decode("utf-8", errors="replace")
        lines = data.splitlines()
        if size > 64_000 and lines:
            lines = lines[1:]  # first line is probably cut in the middle
        return lines[-self.INITIAL_LINES :]


# --- Report & transport ---------------------------------------------------------------------


def os_description(os_release: str = "/etc/os-release") -> str:
    """PRETTY_NAME from os-release (parsed by hand: platform.freedesktop_os_release is 3.10+)."""
    try:
        with open(os_release, encoding="utf-8") as fh:
            for line in fh:
                key, _, value = line.strip().partition("=")
                if key == "PRETTY_NAME" and value:
                    return value.strip("\"'")
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}".strip()


def build_report(
    metrics: dict[str, Any], services: list[dict[str, str]], logs: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "hostname": socket.getfqdn() or socket.gethostname(),
        "os": os_description()[:255],
        "agent_version": __version__,
        "timestamp": _now_iso(),
        "metrics": metrics,
        "services": services,
        "logs": logs,
    }


class FatalSendError(Exception):
    """Retrying will not help (bad token, payload rejected)."""


class Sender:
    RETRY_STATUSES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        config: Config,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Token {config.token}",
                "User-Agent": f"nightwatch-agent/{__version__}",
            }
        )
        self.sleep = sleep
        self.buffer: deque[dict[str, Any]] = deque(maxlen=config.buffer_size)

    def send(self, report: dict[str, Any]) -> bool:
        """Queue the report and flush the queue oldest first. True if everything was accepted."""
        if len(self.buffer) == self.buffer.maxlen:
            log.warning("Buffer full (%d reports): dropping the oldest one", len(self.buffer))
        self.buffer.append(report)

        all_accepted = True
        while self.buffer:
            try:
                delivered = self._post_with_retries(self.buffer[0])
            except FatalSendError as exc:
                # Re-sending the same report can never succeed: drop it, keep the rest.
                log.error("Report rejected, dropping it: %s", exc)
                self.buffer.popleft()
                all_accepted = False
                continue
            if not delivered:
                log.warning("Backend unreachable; %d report(s) buffered", len(self.buffer))
                return False
            self.buffer.popleft()
        return all_accepted

    def _post_with_retries(self, report: dict[str, Any]) -> bool:
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self.session.post(
                    self.config.ingest_url,
                    json=report,
                    timeout=self.config.timeout,
                    verify=self.config.verify_tls,
                )
            except requests.RequestException as exc:
                log.warning("Attempt %d: network error: %s", attempt + 1, exc)
                delay = self._backoff(attempt)
            else:
                if response.status_code < 300:
                    return True
                if response.status_code in (401, 403):
                    raise FatalSendError(f"HTTP {response.status_code}: check the agent token")
                if response.status_code not in self.RETRY_STATUSES:
                    raise FatalSendError(f"HTTP {response.status_code}: {response.text[:300]}")
                log.warning("Attempt %d: HTTP %d", attempt + 1, response.status_code)
                delay = self._retry_after(response) or self._backoff(attempt)

            if attempt < self.config.max_retries:
                self.sleep(delay)
        return False

    @staticmethod
    def _backoff(attempt: int) -> float:
        # Exponential backoff with "full jitter": spreads retries of many agents over time.
        return random.uniform(0, min(30.0, 2.0**attempt))  # noqa: S311 - not crypto

    @staticmethod
    def _retry_after(response: requests.Response) -> float | None:
        try:
            return min(60.0, float(response.headers.get("Retry-After", "")))
        except ValueError:
            return None


# --- Main loop ---------------------------------------------------------------------------------


class Agent:
    def __init__(self, config: Config, sender: Sender | None = None) -> None:
        self.config = config
        self.state = StateStore(config.state_file)
        self.collector = MetricsCollector(config.disk_path)
        self.journal = JournalReader(config, self.state) if config.journal_enabled else None
        self.files = FileTailer(config, self.state)
        self.sender = sender or Sender(config)
        self.stop_event = threading.Event()

    def collect(self) -> dict[str, Any]:
        logs: list[dict[str, Any]] = []
        if self.journal:
            logs.extend(self.journal.read())
        logs.extend(self.files.read())
        logs = logs[-self.config.max_log_lines :]
        report = build_report(
            self.collector.collect(), collect_services(self.config.services), logs
        )
        self.state.save()
        return report

    def run_once(self, dry_run: bool = False) -> bool:
        report = self.collect()
        if dry_run:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return True
        ok = self.sender.send(report)
        if ok:
            log.info(
                "Report sent: cpu=%.1f%% mem=%.1f%% logs=%d",
                report["metrics"]["cpu_percent"],
                report["metrics"]["memory_percent"],
                len(report["logs"]),
            )
        return ok

    def run_forever(self) -> None:
        log.info(
            "NightWatch agent %s started: %s every %ss",
            __version__,
            self.config.ingest_url,
            self.config.interval,
        )
        # Let the CPU counter accumulate a meaningful first sample.
        self.stop_event.wait(1)
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.run_once()
            except Exception:  # keep the daemon alive whatever happens in one cycle
                log.exception("Unexpected error during collection cycle")
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(1.0, self.config.interval - elapsed))
        log.info("Agent stopped.")

    def stop(self, *_args) -> None:
        self.stop_event.set()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NightWatch monitoring agent")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to agent.yaml")
    parser.add_argument("--once", action="store_true", help="collect and send one report, exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the report instead of sending"
    )
    parser.add_argument("--version", action="version", version=f"nightwatch-agent {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=config.log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    agent = Agent(config)

    if args.once or args.dry_run:
        time.sleep(0.5)  # give cpu_percent a measurement window
        return 0 if agent.run_once(dry_run=args.dry_run) else 1

    signal.signal(signal.SIGTERM, agent.stop)
    signal.signal(signal.SIGINT, agent.stop)
    agent.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
