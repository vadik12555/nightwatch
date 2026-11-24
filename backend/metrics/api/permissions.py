from rest_framework.permissions import BasePermission

from servers.models import Server


class IsAgent(BasePermission):
    """Allows access only to requests authenticated with a server token."""

    message = "Server token required."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.auth, Server)
