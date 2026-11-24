"""Rendering LLM output safely.

Model output is untrusted: it is built from log lines that an attacker can influence
(e.g. a crafted URL in an nginx error). So Markdown is converted to HTML and then
sanitized with an allowlist (nh3) — no scripts, no event handlers, no javascript: links.
Suggested shell commands are only *shown* with a copy button, never executed.
"""

import re

import markdown
import nh3
from django.utils.safestring import SafeString, mark_safe

ALLOWED_TAGS = {
    "p", "br", "strong", "em", "code", "pre", "blockquote", "hr",
    "ul", "ol", "li", "h2", "h3", "h4", "h5", "h6", "a",
    "table", "thead", "tbody", "tr", "th", "td",
}  # fmt: skip
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}, "ol": {"start"}}

CODE_BLOCK = re.compile(r"```(?:bash|sh|shell|console|zsh)?[ \t]*\n(.*?)```", re.S | re.I)
INDENTED_FENCE = re.compile(r"^([ \t]+)(```|~~~)")


def _dedent_fences(text: str) -> str:
    """LLMs often indent ``` blocks inside list items; Python-Markdown only sees fences at
    column 0, so such blocks would render as plain text. Move them to the left margin."""
    lines, out, indent = text.splitlines(), [], None
    for line in lines:
        match = INDENTED_FENCE.match(line)
        if indent is None and match:
            indent = match.group(1)
            out.append(line[len(indent) :])
        elif indent is not None:
            out.append(line.removeprefix(indent))
            if line.strip().startswith(("```", "~~~")):
                indent = None
        else:
            out.append(line)
    return "\n".join(out)


def render_markdown(text: str) -> SafeString:
    html = markdown.markdown(
        _dedent_fences(text or ""), extensions=["fenced_code", "sane_lists", "tables"]
    )
    clean = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes={"http", "https"},
        link_rel="noopener noreferrer nofollow",
    )
    return mark_safe(clean)  # noqa: S308 - sanitized by nh3 right above


def extract_commands(text: str) -> list[str]:
    """Shell snippets from ```bash blocks, with "$ " prompts removed."""
    commands = []
    for block in CODE_BLOCK.findall(text or ""):
        lines = [re.sub(r"^\s*\$\s+", "", line) for line in block.strip().splitlines()]
        snippet = "\n".join(lines).strip()
        if snippet:
            commands.append(snippet)
    return commands
