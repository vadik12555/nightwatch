from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from alerts.models import Alert, AlertRule
from notifications.models import TelegramChat, TelegramLinkCode
from servers.models import Server

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def make_alert(server, *, key="offline", status="open", **fields):
    defaults = {
        "kind": Alert.Kind.OFFLINE,
        "severity": "critical",
        "message": "Агент не выходит на связь больше 2 мин",
        "opened_at": timezone.now() - timedelta(minutes=7),
    }
    defaults.update(fields)
    alert = Alert.objects.create(server=server, key=key, status=status, **defaults)
    if status == "resolved":
        alert.resolved_at = timezone.now()
        alert.save()
    return alert


class TestAccess:
    @pytest.mark.parametrize(
        "name", ["alerts:list", "alerts:rules", "alerts:rule_create", "notifications:telegram"]
    )
    def test_requires_login(self, client, name):
        response = client.get(reverse(name))
        assert response.status_code == 302
        assert "/login/" in response["Location"]


class TestAlertList:
    def test_open_alerts_by_default(self, auth_client, server):
        make_alert(server)
        make_alert(server, key="service:nginx", status="resolved", message="nginx failed")

        response = auth_client.get(reverse("alerts:list"))
        html = response.content.decode()

        assert [a.key for a in response.context["page"]] == ["offline"]
        assert "Агент не выходит на связь" in html
        assert "идёт 7 мин" in html
        assert 'class="nav__badge">1<' in html  # badge in the top navigation

    def test_history_and_server_filter(self, auth_client, server):
        other, _ = Server.create_with_token(name="other")
        make_alert(server, status="resolved")
        make_alert(other, status="resolved")

        response = auth_client.get(
            reverse("alerts:list"), {"status": "resolved", "server": server.pk}
        )
        [alert] = response.context["page"]
        assert alert.server == server
        assert "длился" in response.content.decode()

    def test_all_tab_and_unknown_status(self, auth_client, server):
        make_alert(server)
        make_alert(server, key="x", status="resolved")
        assert len(auth_client.get(reverse("alerts:list"), {"status": "all"}).context["page"]) == 2
        assert (
            auth_client.get(reverse("alerts:list"), {"status": "bogus"}).context["status"] == "open"
        )

    def test_htmx_returns_partial(self, auth_client, server):
        html = auth_client.get(reverse("alerts:list"), **HTMX).content.decode()
        assert 'id="alert-table"' in html
        assert "<html" not in html
        assert "Активных алертов нет" in html

    def test_alert_banner_and_dashboard_tile(self, auth_client, server):
        make_alert(server)
        detail = auth_client.get(reverse("servers:detail", args=[server.pk])).content.decode()
        assert "Активные алерты: 1" in detail

        dashboard = auth_client.get(reverse("servers:dashboard"))
        assert dashboard.context["summary"]["open_alerts"] == 1
        assert "Активных алертов: 1" in dashboard.content.decode()


class TestRules:
    def test_default_rules_from_migration(self, auth_client):
        response = auth_client.get(reverse("alerts:rules"))
        html = response.content.decode()
        assert "Высокая загрузка CPU" in html
        assert "CPU &gt; 90.0% дольше 5 мин" in html
        assert "дольше 2 минут" in html  # built-in offline check

    def test_create_rule(self, auth_client, server):
        response = auth_client.post(
            reverse("alerts:rule_create"),
            {
                "name": "Load high",
                "metric": "load_1",
                "condition": "gte",
                "threshold": "4",
                "duration": "600",
                "severity": "critical",
                "server": server.pk,
                "is_enabled": "on",
            },
        )
        assert response.status_code == 302
        rule = AlertRule.objects.get(name="Load high")
        assert rule.server == server
        assert rule.expression == "Load average 1m ≥ 4.00 дольше 10 мин"

    def test_percent_threshold_over_100_rejected(self, auth_client):
        response = auth_client.post(
            reverse("alerts:rule_create"),
            {
                "name": "Bad",
                "metric": "cpu_percent",
                "condition": "gt",
                "threshold": "150",
                "duration": "300",
                "severity": "warning",
            },
        )
        assert response.status_code == 200
        assert "не больше 100" in response.content.decode()
        assert not AlertRule.objects.filter(name="Bad").exists()

    def test_edit_toggle_delete(self, auth_client, server):
        rule = AlertRule.objects.get(name="Высокая загрузка CPU")
        alert = make_alert(server, key=f"rule:{rule.pk}", kind="metric", rule=rule)

        auth_client.post(
            reverse("alerts:rule_edit", args=[rule.pk]),
            {
                "name": "CPU!",
                "metric": "cpu_percent",
                "condition": "gt",
                "threshold": "80",
                "duration": "60",
                "severity": "warning",
                "is_enabled": "on",
            },
        )
        rule.refresh_from_db()
        assert (rule.name, rule.threshold, rule.duration) == ("CPU!", 80, 60)

        auth_client.post(reverse("alerts:rule_toggle", args=[rule.pk]))
        rule.refresh_from_db()
        assert rule.is_enabled is False

        auth_client.post(reverse("alerts:rule_delete", args=[rule.pk]))
        assert not AlertRule.objects.filter(pk=rule.pk).exists()
        alert.refresh_from_db()
        assert alert.rule is None  # history is kept

    def test_toggle_requires_post(self, auth_client):
        rule = AlertRule.objects.first()
        assert auth_client.get(reverse("alerts:rule_toggle", args=[rule.pk])).status_code == 405


class TestTelegramPage:
    def test_not_configured_warning(self, auth_client):
        html = auth_client.get(reverse("notifications:telegram")).content.decode()
        assert "Бот не настроен" in html
        assert "Получить код" in html

    def test_issue_code_shows_command_and_deep_link(self, auth_client, user):
        response = auth_client.post(reverse("notifications:issue_code"), follow=True)
        code = TelegramLinkCode.objects.get(user=user)
        html = response.content.decode()

        assert f"/start {code.code}" in html
        assert f"https://t.me/nightwatch_test_bot?start={code.code}" in html

    def test_expired_code_is_not_shown(self, auth_client, user):
        auth_client.post(reverse("notifications:issue_code"))
        TelegramLinkCode.objects.update(expires_at=timezone.now())
        assert auth_client.get(reverse("notifications:telegram")).context["code"] is None

    def test_chats_toggle_and_unlink(self, auth_client, user):
        chat = TelegramChat.objects.create(user=user, chat_id=1, username="alice_tg")
        html = auth_client.get(reverse("notifications:telegram"), **HTMX).content.decode()
        assert "@alice_tg" in html

        auth_client.post(reverse("notifications:toggle_chat", args=[chat.pk]))
        chat.refresh_from_db()
        assert chat.notify is False

        auth_client.post(reverse("notifications:unlink_chat", args=[chat.pk]))
        assert not TelegramChat.objects.exists()

    def test_cannot_touch_someone_elses_chat(self, auth_client, django_user_model):
        bob = django_user_model.objects.create_user("bob", password="x")
        chat = TelegramChat.objects.create(user=bob, chat_id=2)
        assert (
            auth_client.post(reverse("notifications:unlink_chat", args=[chat.pk])).status_code
            == 404
        )
        assert TelegramChat.objects.filter(pk=chat.pk).exists()
