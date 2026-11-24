from django import forms

from .models import Server


class ServerForm(forms.ModelForm):
    class Meta:
        model = Server
        fields = ["name"]
        widgets = {
            "name": forms.TextInput(
                attrs={"placeholder": "например, prod-web-01", "autofocus": True}
            ),
        }
        help_texts = {
            "name": "Как сервер будет называться в NightWatch. Hostname агент пришлёт сам.",
        }

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if Server.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("Сервер с таким названием уже есть.")
        return name
