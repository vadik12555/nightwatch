from django.contrib.auth.models import AnonymousUser
from rest_framework import authentication, exceptions

from servers.models import Server


class ServerTokenAuthentication(authentication.BaseAuthentication):
    """Authenticates agents by the `Authorization: Token <token>` header.

    On success DRF sets `request.user` to AnonymousUser (an agent is not a human
    user) and `request.auth` to the `Server` the token belongs to.
    """

    keyword = "Token"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != self.keyword.lower().encode():
            return None  # not our scheme: let other authenticators try / 401 later

        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Invalid token header.")
        try:
            raw_token = header[1].decode()
        except UnicodeDecodeError as exc:
            raise exceptions.AuthenticationFailed("Invalid token header.") from exc

        # Lookup is by SHA-256 hash via a unique index: no full token is stored or compared.
        server = Server.objects.by_token(raw_token)
        if server is None:
            raise exceptions.AuthenticationFailed("Invalid token.")
        return AnonymousUser(), server

    def authenticate_header(self, request) -> str:
        # Makes DRF answer 401 (with WWW-Authenticate) instead of 403 for missing credentials.
        return self.keyword
