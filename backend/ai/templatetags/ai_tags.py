from django import template

from ai.rendering import extract_commands, render_markdown

register = template.Library()


@register.filter
def markdown(text: str):
    """LLM / runbook Markdown → sanitized HTML."""
    return render_markdown(text)


@register.filter
def shell_commands(text: str) -> list[str]:
    return extract_commands(text)
