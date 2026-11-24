from types import SimpleNamespace

import openai
import pytest

from ai import chunking, fingerprint, providers, rendering
from ai.models import EMBEDDING_DIMENSIONS


class TestFingerprint:
    def test_variable_parts_are_ignored(self):
        a = fingerprint.fingerprint(
            "gunicorn", "Worker with pid 4242 was terminated due to signal 9"
        )
        b = fingerprint.fingerprint("gunicorn", "Worker with pid 17 was terminated due to signal 9")
        assert a == b

    def test_ips_uuids_and_hex(self):
        message = "client: 185.12.3.4:5555, req 3f2a9c1e-1111-2222-3333-444455556666"
        assert fingerprint.normalize(message + " at 0x7ffd, id deadbeef01") == (
            "client: <ip>, req <uuid> at <hex>, id <hex>"
        )

    def test_different_errors_or_sources_differ(self):
        base = fingerprint.fingerprint("nginx", "upstream timed out")
        assert base != fingerprint.fingerprint("nginx", "connection refused")
        assert base != fingerprint.fingerprint("haproxy", "upstream timed out")
        assert base == fingerprint.fingerprint("NGINX ", "upstream   timed out")


class TestChunking:
    DOC = """# Диск

Вступление.

## Диагностика

```bash
# это комментарий, а не заголовок
df -h
```

## Починка

### PostgreSQL

Освободить место.
"""

    def test_sections_with_heading_paths(self):
        chunks = chunking.split_markdown(self.DOC)
        assert [c.heading for c in chunks] == [
            "Диск",
            "Диск / Диагностика",
            "Диск / Починка / PostgreSQL",
        ]
        assert "# это комментарий" in chunks[1].text
        assert chunks[2].embed_text == "Диск / Починка / PostgreSQL\n\nОсвободить место."

    def test_long_section_is_split_with_overlap(self):
        paragraphs = [f"Абзац {i}. " + "слово " * 30 for i in range(12)]
        doc = "# Большой раздел\n\n" + "\n\n".join(paragraphs)

        chunks = chunking.split_markdown(doc, max_chars=500, overlap=250)

        assert len(chunks) > 3
        assert all(len(c.text) <= 500 for c in chunks)
        # the last paragraph of a chunk starts the next one (overlap)
        assert chunks[1].text.split("\n\n")[0] == chunks[0].text.split("\n\n")[-1]

    def test_code_block_is_not_cut(self):
        code = "```bash\n" + "\n\n".join(f"echo {i}" for i in range(40)) + "\n```"
        doc = "# A\n\n" + "текст " * 50 + "\n\n" + code
        chunks = chunking.split_markdown(doc, max_chars=600, overlap=100)
        assert any(c.text.startswith("```bash") and c.text.endswith("```") for c in chunks)

    def test_empty_document(self):
        assert chunking.split_markdown("") == []
        assert chunking.split_markdown("# Только заголовок\n") == []


class TestRendering:
    def test_dangerous_html_is_removed(self):
        html = rendering.render_markdown(
            "**жирный** <script>alert(1)</script> [ссылка](javascript:alert(1)) "
            '<img src=x onerror="alert(1)">'
        )
        assert "<strong>жирный</strong>" in html
        assert "<script" not in html
        assert "javascript:" not in html
        assert "onerror" not in html

    def test_fences_indented_inside_lists_still_render_as_code(self):
        text = "1. **Проверить место**:\n   ```bash\n   df -h\n   ```\n2. Дальше"
        html = rendering.render_markdown(text)
        assert "<pre><code" in html
        assert "df -h" in html
        assert rendering.extract_commands(text) == ["df -h"]

    def test_code_blocks_and_commands(self):
        text = (
            "Шаги:\n```bash\n$ sudo journalctl --vacuum-size=500M\ndf -h\n```\n```python\nx=1\n```"
        )
        assert "<pre><code" in rendering.render_markdown(text)
        assert rendering.extract_commands(text) == ["sudo journalctl --vacuum-size=500M\ndf -h"]


# --- Provider -----------------------------------------------------------------------------


class FakeOpenAIClient:
    def __init__(self, *, content="ответ", vectors=None, error=None):
        self.calls = []
        self.error = error

        def create_chat(**kwargs):
            self.calls.append(("chat", kwargs))
            if self.error:
                raise self.error
            message = SimpleNamespace(content=content)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        def create_embeddings(**kwargs):
            self.calls.append(("embed", kwargs))
            if self.error:
                raise self.error
            items = [
                SimpleNamespace(index=i, embedding=(vectors or [0.0] * EMBEDDING_DIMENSIONS))
                for i in range(len(kwargs["input"]))
            ]
            return SimpleNamespace(data=list(reversed(items)))  # API may return any order

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create_chat))
        self.embeddings = SimpleNamespace(create=create_embeddings)


def make_provider(**client_kwargs):
    client = FakeOpenAIClient(**client_kwargs)
    provider = providers.OpenAICompatibleProvider(
        base_url="http://llm.test/v1",
        api_key="k",
        model="m",
        embedding_model="e",
        client=client,
    )
    return provider, client


class TestOpenAICompatibleProvider:
    def test_chat(self):
        provider, client = make_provider(content="  Привет  ")
        assert provider.chat([{"role": "user", "content": "hi"}]) == "Привет"
        kind, kwargs = client.calls[0]
        assert kind == "chat"
        assert kwargs["model"] == "m"

    def test_empty_answer_is_an_error(self):
        provider, _ = make_provider(content="   ")
        with pytest.raises(providers.LLMError, match="пустой"):
            provider.chat([{"role": "user", "content": "hi"}])

    def test_api_errors_are_wrapped(self):
        provider, _ = make_provider(error=openai.OpenAIError("connection reset"))
        with pytest.raises(providers.LLMError, match="не удался"):
            provider.chat([{"role": "user", "content": "hi"}])
        with pytest.raises(providers.LLMError):
            provider.embed(["x"])

    def test_embed_batches_and_dimensions(self):
        provider, client = make_provider()
        vectors = provider.embed([f"text {i}" for i in range(70)])

        assert len(vectors) == 70
        batches = [kwargs["input"] for kind, kwargs in client.calls]
        assert [len(b) for b in batches] == [32, 32, 6]
        assert client.calls[0][1]["dimensions"] == EMBEDDING_DIMENSIONS

    def test_wrong_dimensions_rejected(self):
        provider, _ = make_provider(vectors=[0.1] * 768)
        with pytest.raises(providers.LLMError, match="768 измерений"):
            provider.embed(["x"])

    def test_get_provider_requires_key(self, settings):
        settings.LLM_API_KEY = ""
        assert not providers.is_configured()
        with pytest.raises(providers.AINotConfiguredError):
            providers.get_provider()

        settings.LLM_API_KEY = "ollama"
        provider = providers.get_provider()
        assert provider.model == settings.LLM_MODEL
        assert str(provider.client.base_url).startswith(settings.LLM_BASE_URL)
