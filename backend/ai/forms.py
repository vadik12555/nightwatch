from django import forms

MAX_RUNBOOK_BYTES = 500_000
ALLOWED_EXTENSIONS = (".md", ".markdown", ".txt")


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault(
            "widget", MultipleFileInput(attrs={"accept": ",".join(ALLOWED_EXTENSIONS)})
        )
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        files = data if isinstance(data, list | tuple) else [data] if data else []
        return [super(MultipleFileField, self).clean(file, initial) for file in files]


class RunbookUploadForm(forms.Form):
    files = MultipleFileField(label="Markdown-файлы", required=False)
    title = forms.CharField(label="Название", max_length=200, required=False)
    content = forms.CharField(
        label="…или вставьте текст",
        required=False,
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": "# Что делать, если…"}),
    )

    def clean_files(self):
        documents = []
        for upload in self.cleaned_data["files"]:
            if not upload.name.lower().endswith(ALLOWED_EXTENSIONS):
                raise forms.ValidationError(f"{upload.name}: нужен .md, .markdown или .txt")
            if upload.size > MAX_RUNBOOK_BYTES:
                raise forms.ValidationError(f"{upload.name}: файл больше 500 КБ")
            try:
                text = upload.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError(f"{upload.name}: файл не в кодировке UTF-8") from exc
            documents.append((upload.name, text))
        return documents

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("files") and not (cleaned.get("content") or "").strip():
            raise forms.ValidationError("Выберите файлы или вставьте текст runbook'а.")
        if (cleaned.get("content") or "").strip() and not (cleaned.get("title") or "").strip():
            self.add_error("title", "Укажите название для вставленного текста.")
        return cleaned


class ChatForm(forms.Form):
    question = forms.CharField(max_length=2000, strip=True)
    server = forms.IntegerField(required=False)
