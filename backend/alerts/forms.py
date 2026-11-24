from django import forms

from .models import AlertRule


class AlertRuleForm(forms.ModelForm):
    class Meta:
        model = AlertRule
        fields = [
            "name",
            "metric",
            "condition",
            "threshold",
            "duration",
            "severity",
            "server",
            "is_enabled",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "например, Высокая загрузка CPU"}),
            "threshold": forms.NumberInput(attrs={"step": "any", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["server"].empty_label = "Все серверы"

    def clean(self):
        cleaned = super().clean()
        metric, threshold = cleaned.get("metric"), cleaned.get("threshold")
        if metric in AlertRule.PERCENT_METRICS and threshold is not None and threshold > 100:
            self.add_error("threshold", "Для метрик в процентах порог не больше 100.")
        return cleaned
