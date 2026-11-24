"""Sensible default rules, so a fresh install alerts out of the box.

Offline servers and failed systemd services are built-in checks and need no rule.
"""

from django.db import migrations

DEFAULT_RULES = [
    # name, metric, condition, threshold, duration (s), severity
    ("Высокая загрузка CPU", "cpu_percent", "gt", 90, 300, "warning"),
    ("Заканчивается память", "memory_percent", "gt", 90, 300, "critical"),
    ("Заканчивается место на диске", "disk_percent", "gt", 90, 60, "critical"),
]


def create_default_rules(apps, schema_editor):
    AlertRule = apps.get_model("alerts", "AlertRule")
    for name, metric, condition, threshold, duration, severity in DEFAULT_RULES:
        AlertRule.objects.get_or_create(
            name=name,
            defaults={
                "metric": metric,
                "condition": condition,
                "threshold": threshold,
                "duration": duration,
                "severity": severity,
            },
        )


def delete_default_rules(apps, schema_editor):
    AlertRule = apps.get_model("alerts", "AlertRule")
    AlertRule.objects.filter(name__in=[rule[0] for rule in DEFAULT_RULES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("alerts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_default_rules, delete_default_rules),
    ]
