import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import CommandObject
from aiogram.methods import SendMessage
from django.utils import timezone

from alerts.models import Alert
from notifications import bot, formatting, services, tasks, telegram
from notifications.models import TelegramChat, TelegramLinkCode
from servers.models import Server

METHOD = SendMessage(chat_id=1, text="x")
CHAT_ID = 777


@pytest.fixture
def linked_chat(user):
    return TelegramChat.objects.create(user=user, chat_id=CHAT_ID, username="alice_tg")


@pytest.fixture
def alert(server):
    return Alert.objects.create(
        server=server,
        kind=Alert.Kind.SERVICE,
        key="service:nginx",
        severity="critical",
        message="Сервис nginx в состоянии failed",
        opened_at=timezone.now() - timedelta(minutes=12),
    )


# --- Linking --------------------------------------------------------------------------


@pytest.mark.django_db
class TestLinking:
    def test_valid_code_links_chat(self, user):
        code = TelegramLinkCode.issue(user)

        reply = services.link_chat(code.code, CHAT_ID, "alice_tg", "Alice")

        assert "привязан к аккаунту <b>alice</b>" in reply
        chat = TelegramChat.objects.get(chat_id=CHAT_ID)
        assert (chat.user, chat.username, chat.title) == (user, "alice_tg", "Alice")
        code.refresh_from_db()
        assert code.used_at is not None

    def test_code_is_single_use(self, user):
        code = TelegramLinkCode.issue(user)
        services.link_chat(code.code, CHAT_ID)
        reply = services.link_chat(code.code, 999)
        assert "Код не подошёл" in reply
        assert not TelegramChat.objects.filter(chat_id=999).exists()

    def test_expired_and_unknown_codes(self, user):
        code = TelegramLinkCode.issue(user)
        TelegramLinkCode.objects.filter(pk=code.pk).update(expires_at=timezone.now())
        assert "Код не подошёл" in services.link_chat(code.code, CHAT_ID)
        assert "Код не подошёл" in services.link_chat("nope", CHAT_ID)
        assert not TelegramChat.objects.exists()

    def test_new_code_invalidates_previous(self, user):
        first = TelegramLinkCode.issue(user)
        TelegramLinkCode.issue(user)
        first.refresh_from_db()
        assert not first.is_valid

    def test_relinking_chat_moves_it_to_new_user(self, user, django_user_model, linked_chat):
        bob = django_user_model.objects.create_user("bob", password="x")
        services.link_chat(TelegramLinkCode.issue(bob).code, CHAT_ID)
        assert TelegramChat.objects.get(chat_id=CHAT_ID).user == bob

    def test_unlink(self, linked_chat):
        assert "отвязан" in services.unlink_chat(CHAT_ID)
        assert "и так не привязан" in services.unlink_chat(CHAT_ID)


# --- Commands -------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommands:
    def test_unlinked_chat_cannot_read_data(self, server):
        assert services.status_text(CHAT_ID) == formatting.NOT_LINKED
        assert services.servers_text(CHAT_ID) == formatting.NOT_LINKED
        assert services.help_text(CHAT_ID) == formatting.NOT_LINKED

    def test_status_lists_fleet_and_open_alerts(self, linked_chat, server, alert):
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())

        text = services.status_text(CHAT_ID)

        assert "Серверы: 1 · 🟢 1 · 🔴 0" in text
        assert "Активные алерты: 1" in text
        assert "<b>web-01</b>: Сервис nginx в состоянии failed (12 мин)" in text

    def test_status_without_alerts(self, linked_chat, server):
        assert "Активных алертов нет" in services.status_text(CHAT_ID)

    def test_servers_shows_metrics_and_states(self, linked_chat, server, alert):
        from tests.test_views import add_snapshot

        add_snapshot(server, cpu_percent=42.0)
        Server.objects.filter(pk=server.pk).update(last_seen=timezone.now())
        offline, _ = Server.create_with_token(name="db-01")
        Server.objects.filter(pk=offline.pk).update(last_seen=timezone.now() - timedelta(hours=3))
        Server.create_with_token(name="new-box")

        text = services.servers_text(CHAT_ID)

        assert "🟢 <b>web-01</b>\n    CPU 42% · RAM 50% · диск 20% · LA 1.50" in text
        assert "🚨 алертов: 1" in text
        assert "🔴 <b>db-01</b>\n    нет связи 3 ч" in text
        assert "⚪ <b>new-box</b>\n    ждёт первый отчёт агента" in text

    def test_server_names_are_html_escaped(self, linked_chat):
        Server.create_with_token(name="<script>")
        assert "&lt;script&gt;" in services.servers_text(CHAT_ID)


def fake_message(chat_id=CHAT_ID, username="alice_tg"):
    return SimpleNamespace(
        chat=SimpleNamespace(id=chat_id, title=None),
        from_user=SimpleNamespace(username=username, first_name="Alice", last_name=None),
        answer=AsyncMock(),
    )


@pytest.mark.django_db(transaction=True)  # handlers hit the DB from sync_to_async threads
class TestHandlers:
    def test_start_with_code_links_chat(self, user):
        code = TelegramLinkCode.issue(user)
        message = fake_message()

        asyncio.run(bot.cmd_start(message, CommandObject(command="start", args=code.code)))

        assert "Чат привязан" in message.answer.await_args.args[0]
        assert TelegramChat.objects.get(chat_id=CHAT_ID).title == "Alice"

    def test_start_without_code(self, user):
        message = fake_message()
        asyncio.run(bot.cmd_start(message, CommandObject(command="start")))
        assert message.answer.await_args.args[0] == formatting.NOT_LINKED

    def test_status_and_servers_commands(self, user):
        TelegramChat.objects.create(user=user, chat_id=CHAT_ID)
        Server.create_with_token(name="web-01")

        status, servers = fake_message(), fake_message()
        asyncio.run(bot.cmd_status(status))
        asyncio.run(bot.cmd_servers(servers))

        assert "NightWatch · сводка" in status.answer.await_args.args[0]
        assert "web-01" in servers.answer.await_args.args[0]

    def test_dispatcher_has_all_commands(self):
        dispatcher = bot.build_dispatcher()
        assert bot.router in dispatcher.sub_routers
        assert {c.command for c in bot.COMMANDS} == {"status", "servers", "unlink", "help"}


# --- Formatting -------------------------------------------------------------------------


@pytest.mark.django_db
class TestFormatting:
    def test_opened_message(self, alert):
        text = formatting.alert_message(alert, "opened")
        assert text.startswith(
            "🔴 <b>CRITICAL</b> · <b>web-01</b>\nСервис nginx в состоянии failed"
        )

    def test_resolved_message_shows_duration(self, alert):
        alert.status, alert.resolved_at = (
            Alert.Status.RESOLVED,
            alert.opened_at + timedelta(minutes=12),
        )
        text = formatting.alert_message(alert, "resolved")
        assert text.startswith("✅ <b>Восстановлено</b> · <b>web-01</b>")
        assert text.endswith("длилось 12 мин")

    def test_server_url_only_with_public_url(self, server, settings):
        assert formatting.server_url(server) is None
        settings.NIGHTWATCH_PUBLIC_URL = "https://nw.example.com"
        assert formatting.server_url(server) == f"https://nw.example.com/servers/{server.pk}/"

    @pytest.mark.parametrize(
        ("seconds", "text"),
        [
            (5, "5 с"),
            (125, "2 мин"),
            (3600, "1 ч"),
            (3 * 3600 + 600, "3 ч 10 мин"),
            (90000, "1 д 1 ч"),
        ],
    )
    def test_humanize_duration(self, seconds, text):
        assert formatting.humanize_duration(seconds) == text


# --- Notification task -----------------------------------------------------------------------


@pytest.fixture
def configured(settings):
    settings.TELEGRAM_BOT_TOKEN = "123456:test-token"


@pytest.fixture
def fake_send(monkeypatch):
    calls = []
    outcomes = {}

    def send(chat_ids, text, url=None):
        calls.append((list(chat_ids), text, url))
        return {chat_id: outcomes.get(chat_id) for chat_id in chat_ids}

    monkeypatch.setattr(tasks.telegram, "send_messages", send)
    send.calls, send.outcomes = calls, outcomes
    return send


@pytest.mark.django_db
class TestNotificationTask:
    def test_skipped_without_token(self, alert, linked_chat, fake_send):
        assert tasks.send_alert_notification(alert.pk, "opened") == 0
        assert fake_send.calls == []

    def test_sends_to_linked_chats_that_want_notifications(
        self, configured, alert, linked_chat, user, fake_send
    ):
        TelegramChat.objects.create(user=user, chat_id=888, notify=False)

        assert tasks.send_alert_notification(alert.pk, "opened") == 1

        [(chat_ids, text, url)] = fake_send.calls
        assert chat_ids == [CHAT_ID]
        assert "CRITICAL" in text
        assert url is None

    def test_blocked_bot_unlinks_chat(self, configured, alert, linked_chat, fake_send):
        fake_send.outcomes[CHAT_ID] = TelegramForbiddenError(method=METHOD, message="blocked")
        assert tasks.send_alert_notification(alert.pk, "opened") == 0
        assert not TelegramChat.objects.exists()

    def test_network_error_retries_only_failed_chats(
        self, configured, alert, linked_chat, user, fake_send, monkeypatch
    ):
        TelegramChat.objects.create(user=user, chat_id=888)
        fake_send.outcomes[888] = TelegramNetworkError(method=METHOD, message="timeout")
        retried = {}

        def fake_retry(**kwargs):
            retried.update(kwargs)
            return RuntimeError("retry")

        monkeypatch.setattr(tasks.send_alert_notification, "retry", fake_retry)

        with pytest.raises(RuntimeError, match="retry"):
            tasks.send_alert_notification(alert.pk, "opened")
        assert retried["kwargs"]["chat_ids"] == [888]

    def test_missing_alert(self, configured, fake_send):
        assert tasks.send_alert_notification(123456, "opened") == 0


class FakeBot:
    def __init__(self, failures):
        self.failures = list(failures)
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.failures:
            raise self.failures.pop(0)
        self.sent.append((chat_id, text, reply_markup))


def test_send_messages_waits_on_flood_control(monkeypatch):
    fake = FakeBot([TelegramRetryAfter(method=METHOD, message="flood", retry_after=0)])
    monkeypatch.setattr(telegram, "make_bot", lambda: fake)

    results = telegram.send_messages([1], "hi", url="https://nw.example.com/servers/1/")

    assert results == {1: None}
    [(_, _, markup)] = fake.sent
    assert markup.inline_keyboard[0][0].url == "https://nw.example.com/servers/1/"


def test_send_messages_reports_errors_per_chat(monkeypatch):
    error = TelegramForbiddenError(method=METHOD, message="blocked")
    fake = FakeBot([error])
    monkeypatch.setattr(telegram, "make_bot", lambda: fake)

    assert telegram.send_messages([1, 2], "hi") == {1: error, 2: None}
    assert telegram.send_messages([], "hi") == {}
