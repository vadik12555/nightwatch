import json
from datetime import datetime

import pytest
import requests

import nightwatch_agent as na


def write_config(tmp_path, **overrides):
    data = {
        "api_url": "http://backend.local",
        "token": "nw_test",
        "state_file": str(tmp_path / "state.json"),
        "journal": {"enabled": False},
    }
    data.update(overrides)
    path = tmp_path / "agent.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")  # JSON is valid YAML
    return path


@pytest.fixture
def config(tmp_path):
    return na.load_config(write_config(tmp_path))


# --- Config -----------------------------------------------------------------


class TestConfig:
    def test_defaults_and_ingest_url(self, config):
        assert config.interval == 15
        assert config.ingest_url == "http://backend.local/api/v1/ingest/"
        assert config.journal_enabled is False

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NIGHTWATCH_TOKEN", "nw_from_env")
        monkeypatch.setenv("NIGHTWATCH_API_URL", "https://nw.example.com/")
        config = na.load_config(write_config(tmp_path))

        assert config.token == "nw_from_env"
        assert config.ingest_url == "https://nw.example.com/api/v1/ingest/"

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"api_url": "backend.local"}, "api_url"),
            ({"token": ""}, "token"),
            ({"interval": 1}, "interval"),
        ],
    )
    def test_invalid_config(self, tmp_path, monkeypatch, overrides, message):
        monkeypatch.delenv("NIGHTWATCH_TOKEN", raising=False)
        monkeypatch.delenv("NIGHTWATCH_API_URL", raising=False)
        with pytest.raises(na.ConfigError, match=message):
            na.load_config(write_config(tmp_path, **overrides))

    def test_missing_file(self, tmp_path):
        with pytest.raises(na.ConfigError, match="not found"):
            na.load_config(tmp_path / "nope.yaml")


# --- Metrics ------------------------------------------------------------------


class TestMetricsCollector:
    def test_collect_returns_valid_ranges(self):
        metrics = na.MetricsCollector(disk_path="/").collect()

        assert set(metrics) == {
            "cpu_percent",
            "memory_percent",
            "memory_used",
            "memory_total",
            "disk_percent",
            "disk_used",
            "disk_total",
            "net_sent_bps",
            "net_recv_bps",
            "load_1",
            "load_5",
            "load_15",
            "uptime_seconds",
        }
        assert 0 <= metrics["cpu_percent"] <= 100
        assert 0 <= metrics["memory_used"] <= metrics["memory_total"]
        assert 0 <= metrics["disk_used"] <= metrics["disk_total"]
        assert metrics["uptime_seconds"] > 0

    def test_network_rate_from_counter_delta(self, monkeypatch):
        counters = iter([(1_000, 5_000), (3_000, 9_000)])
        clock = iter([100.0, 102.0])

        class Counters:
            def __init__(self, sent, recv):
                self.bytes_sent, self.bytes_recv = sent, recv

        monkeypatch.setattr(na.psutil, "net_io_counters", lambda: Counters(*next(counters)))
        monkeypatch.setattr(na.time, "monotonic", lambda: next(clock))
        collector = na.MetricsCollector()

        assert collector._network_rates() == (0.0, 0.0)  # first sample: no delta yet
        assert collector._network_rates() == (1_000.0, 2_000.0)


# --- Services -----------------------------------------------------------------


class TestServices:
    def test_status_from_systemctl_output(self, monkeypatch):
        class Result:
            stdout = "failed\n"

        monkeypatch.setattr(na.subprocess, "run", lambda *a, **kw: Result())
        assert na.service_status("nginx") == "failed"

    def test_unexpected_output_is_unknown(self, monkeypatch):
        class Result:
            stdout = "something-weird\n"

        monkeypatch.setattr(na.subprocess, "run", lambda *a, **kw: Result())
        assert na.service_status("nginx") == "unknown"

    def test_no_systemctl_is_unknown(self, monkeypatch):
        def missing(*args, **kwargs):
            raise FileNotFoundError

        monkeypatch.setattr(na.subprocess, "run", missing)
        assert na.collect_services(["ssh"]) == [{"name": "ssh", "status": "unknown"}]


# --- Logs ---------------------------------------------------------------------


class TestLogParsing:
    @pytest.mark.parametrize(
        ("line", "level"),
        [
            ("2026-01-01 [error] 123#0: upstream timed out", "error"),
            ("Traceback (most recent call last):", "error"),
            ("WARNING: disk almost full", "warning"),
            ("kernel panic - not syncing", "critical"),
            ("GET /health 200", "info"),
        ],
    )
    def test_detect_level(self, line, level):
        assert na.detect_level(line) == level

    def test_parse_journal_entry(self):
        line = json.dumps(
            {
                "MESSAGE": "Out of memory: Killed process 4242 (python)",
                "PRIORITY": "3",
                "SYSLOG_IDENTIFIER": "kernel",
                "__REALTIME_TIMESTAMP": "1767225600000000",
                "__CURSOR": "s=abc;i=1",
            }
        )
        record, cursor = na.JournalReader.parse_entry(line)

        assert record["level"] == "error"
        assert record["source"] == "kernel"
        assert record["timestamp"].startswith("2026-01-01T00:00:00")
        assert cursor == "s=abc;i=1"

    def test_parse_journal_binary_message(self):
        line = json.dumps({"MESSAGE": list(b"bin\xd0\xbe"), "PRIORITY": 6})
        record, _ = na.JournalReader.parse_entry(line)
        assert record["message"] == "binо"
        assert record["level"] == "info"

    def test_parse_journal_garbage(self):
        assert na.JournalReader.parse_entry("not json") is None
        assert na.JournalReader.parse_entry(json.dumps({"PRIORITY": 3})) is None


class TestFileTailer:
    def make_tailer(self, tmp_path, log_file):
        config = na.load_config(write_config(tmp_path, logs={"files": [str(log_file)]}))
        state = na.StateStore(config.state_file)
        return na.FileTailer(config, state), state

    def test_first_read_tails_then_follows_new_lines(self, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("".join(f"line {i}\n" for i in range(50)), encoding="utf-8")
        tailer, _ = self.make_tailer(tmp_path, log_file)

        first = tailer.read()
        assert len(first) == na.FileTailer.INITIAL_LINES
        assert first[-1]["message"] == "line 49"
        assert first[-1]["source"] == "app.log"

        assert tailer.read() == []  # nothing new

        with log_file.open("a", encoding="utf-8") as fh:
            fh.write("ERROR database is down\npartial line without newline")
        new = tailer.read()
        assert [e["message"] for e in new] == ["ERROR database is down"]
        assert new[0]["level"] == "error"

        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(" — completed\n")
        assert tailer.read()[0]["message"] == "partial line without newline — completed"

    def test_truncated_file_is_read_from_start(self, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("old 1\nold 2\nold 3\n", encoding="utf-8")
        tailer, _ = self.make_tailer(tmp_path, log_file)
        tailer.read()

        log_file.write_text("new\n", encoding="utf-8")  # logrotate copytruncate
        assert [e["message"] for e in tailer.read()] == ["new"]

    def test_position_persists_across_restarts(self, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("a\n", encoding="utf-8")
        tailer, state = self.make_tailer(tmp_path, log_file)
        tailer.read()
        state.save()

        with log_file.open("a", encoding="utf-8") as fh:
            fh.write("b\n")
        restarted, _ = self.make_tailer(tmp_path, log_file)
        assert [e["message"] for e in restarted.read()] == ["b"]

    def test_missing_file_is_skipped(self, tmp_path):
        tailer, _ = self.make_tailer(tmp_path, tmp_path / "absent.log")
        assert tailer.read() == []


# --- Transport ------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


class FakeSession:
    """Replays a scripted sequence of responses/exceptions and records requests."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.headers = {}
        self.sent = []

    def post(self, url, json, timeout, verify):
        self.sent.append(json)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome) if isinstance(outcome, int) else outcome


def make_sender(config, outcomes):
    session = FakeSession(outcomes)
    sleeps = []
    sender = na.Sender(config, session=session, sleep=sleeps.append)
    return sender, session, sleeps


class TestSender:
    def test_sets_auth_header(self, config):
        sender, session, _ = make_sender(config, [201])
        assert session.headers["Authorization"] == "Token nw_test"
        assert sender.send({"n": 1})

    def test_retries_network_errors_then_succeeds(self, config):
        sender, session, sleeps = make_sender(config, [requests.ConnectionError("down"), 503, 201])
        assert sender.send({"n": 1}) is True
        assert len(session.sent) == 3
        assert len(sleeps) == 2
        assert all(0 <= s <= 30 for s in sleeps)
        assert not sender.buffer

    def test_honours_retry_after(self, config):
        sender, _, sleeps = make_sender(config, [FakeResponse(429, {"Retry-After": "7"}), 201])
        assert sender.send({"n": 1})
        assert sleeps == [7.0]

    def test_unreachable_backend_buffers_report(self, config):
        failures = [requests.Timeout("slow")] * (config.max_retries + 1)
        sender, _, _ = make_sender(config, failures)

        assert sender.send({"n": 1}) is False
        assert list(sender.buffer) == [{"n": 1}]

    def test_buffer_is_flushed_oldest_first(self, config):
        failures = [requests.ConnectionError()] * (config.max_retries + 1)
        sender, session, _ = make_sender(config, [*failures, 201, 201])

        sender.send({"n": 1})
        assert sender.send({"n": 2}) is True
        assert session.sent[-2:] == [{"n": 1}, {"n": 2}]
        assert not sender.buffer

    def test_bad_token_is_not_retried(self, config):
        sender, session, sleeps = make_sender(config, [401])
        assert sender.send({"n": 1}) is False
        assert len(session.sent) == 1
        assert sleeps == []
        assert not sender.buffer

    def test_buffer_is_bounded(self, tmp_path):
        config = na.load_config(write_config(tmp_path, buffer_size=2, max_retries=0))
        sender, _, _ = make_sender(config, [requests.ConnectionError()] * 3)
        for n in range(3):
            sender.send({"n": n})
        assert list(sender.buffer) == [{"n": 1}, {"n": 2}]


# --- End to end (without network) ------------------------------------------------------


def test_agent_builds_report_accepted_by_backend_schema(tmp_path, monkeypatch):
    log_file = tmp_path / "app.log"
    log_file.write_text("ERROR boom\n", encoding="utf-8")
    config = na.load_config(
        write_config(tmp_path, services=["nginx"], logs={"files": [str(log_file)]})
    )
    monkeypatch.setattr(na, "service_status", lambda name: "active")
    session = FakeSession([201])
    agent = na.Agent(config, sender=na.Sender(config, session=session, sleep=lambda s: None))

    assert agent.run_once() is True
    report = session.sent[0]
    assert report["agent_version"] == na.__version__
    assert report["services"] == [{"name": "nginx", "status": "active"}]
    assert report["logs"][0]["level"] == "error"
    datetime.fromisoformat(report["timestamp"])
    assert (tmp_path / "state.json").exists()


def test_main_dry_run_prints_report(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(na.time, "sleep", lambda s: None)
    assert na.main(["--config", str(write_config(tmp_path)), "--dry-run"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert "metrics" in printed


def test_main_reports_config_error(tmp_path, capsys):
    assert na.main(["--config", str(tmp_path / "missing.yaml")]) == 2
    assert "Configuration error" in capsys.readouterr().err
