from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model.

    Empty for now, but declared from day one: swapping the user model after the
    first migration is painful, while extending this class later is trivial
    (e.g. a Telegram chat id for alerts in stage 2).
    """

    class Meta(AbstractUser.Meta):
        verbose_name = "пользователь"
        verbose_name_plural = "пользователи"
