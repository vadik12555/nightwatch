from django.contrib.auth.forms import AuthenticationForm


class LoginForm(AuthenticationForm):
    """Standard auth form with placeholders/autocomplete hints for the dark login page."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget.attrs.update(
            {"placeholder": "admin", "autocomplete": "username", "autofocus": True}
        )
        self.fields["password"].widget.attrs.update(
            {"placeholder": "••••••••", "autocomplete": "current-password"}
        )
