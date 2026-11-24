"""Validation of agent reports.

The agent is a remote, untrusted client: every number is range-checked, every
string is length-limited and list sizes are capped, so a buggy or malicious
agent cannot write garbage or huge payloads into the database.
"""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from metrics.models import LogEntry, ServiceStatus

MAX_SERVICES = 100
MAX_LOGS = 500
MAX_LOG_MESSAGE = 8_000
MAX_CLOCK_SKEW = timedelta(minutes=5)


def percent_field() -> serializers.FloatField:
    return serializers.FloatField(min_value=0, max_value=100)


def non_negative_int() -> serializers.IntegerField:
    return serializers.IntegerField(min_value=0)


def non_negative_float() -> serializers.FloatField:
    return serializers.FloatField(min_value=0)


class MetricsSerializer(serializers.Serializer):
    cpu_percent = percent_field()
    memory_percent = percent_field()
    memory_used = non_negative_int()
    memory_total = non_negative_int()
    disk_percent = percent_field()
    disk_used = non_negative_int()
    disk_total = non_negative_int()
    net_sent_bps = non_negative_float()
    net_recv_bps = non_negative_float()
    load_1 = non_negative_float()
    load_5 = non_negative_float()
    load_15 = non_negative_float()
    uptime_seconds = non_negative_int()

    def validate(self, attrs):
        if attrs["memory_used"] > attrs["memory_total"]:
            raise serializers.ValidationError("memory_used cannot exceed memory_total.")
        if attrs["disk_used"] > attrs["disk_total"]:
            raise serializers.ValidationError("disk_used cannot exceed disk_total.")
        return attrs


class ServiceSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(choices=ServiceStatus.State.choices)


class LogSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    level = serializers.ChoiceField(choices=LogEntry.Level.choices)
    source = serializers.CharField(max_length=255)
    # No max_length: an over-long line is truncated, not rejected, so one huge
    # stack trace does not make the whole report fail.
    message = serializers.CharField(trim_whitespace=False)

    def validate_message(self, value: str) -> str:
        if len(value) > MAX_LOG_MESSAGE:
            return value[: MAX_LOG_MESSAGE - 1] + "…"
        return value


class IngestSerializer(serializers.Serializer):
    hostname = serializers.CharField(max_length=255)
    os = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    agent_version = serializers.CharField(
        max_length=32, required=False, allow_blank=True, default=""
    )
    # When the agent took the reading. Buffered reports are re-sent later, so the
    # server must not simply use "now".
    timestamp = serializers.DateTimeField(required=False)
    metrics = MetricsSerializer()
    services = ServiceSerializer(many=True, required=False, max_length=MAX_SERVICES)
    logs = LogSerializer(many=True, required=False, max_length=MAX_LOGS)

    def validate_timestamp(self, value):
        if value > timezone.now() + MAX_CLOCK_SKEW:
            raise serializers.ValidationError("Timestamp is in the future; check the clock (NTP).")
        return value

    def validate_services(self, value):
        names = [service["name"] for service in value]
        if len(names) != len(set(names)):
            raise serializers.ValidationError("Service names must be unique.")
        return value
