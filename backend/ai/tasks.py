from celery import shared_task

from . import reports, services


@shared_task
def explain_log(explanation_id: int) -> str:
    return services.run_explanation(explanation_id).status


@shared_task
def index_runbook(runbook_id: int) -> str:
    return services.run_indexing(runbook_id).status


@shared_task
def answer_chat_message(message_id: int) -> str:
    return services.run_answer(message_id).status


@shared_task
def weekly_health_report() -> dict:
    """Celery beat: Mondays 09:00. Builds the report and sends it to linked Telegram chats."""
    report = reports.build_weekly_report()
    sent = reports.send_report(report)
    return {"report_id": report.pk, "ai": report.ai_generated, "sent_to": sent}
