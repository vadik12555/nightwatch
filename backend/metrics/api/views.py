import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from metrics.services import ingest_report

from .authentication import ServerTokenAuthentication
from .permissions import IsAgent
from .serializers import IngestSerializer
from .throttling import ServerRateThrottle

logger = logging.getLogger(__name__)


class IngestView(APIView):
    """POST /api/v1/ingest/ — receives a metrics/services/logs report from an agent.

    Pipeline: token auth → permission → per-server rate limit → validation → save.
    """

    authentication_classes = [ServerTokenAuthentication]
    permission_classes = [IsAgent]
    throttle_classes = [ServerRateThrottle]

    def post(self, request: Request) -> Response:
        server = request.auth
        serializer = IngestSerializer(data=request.data)
        if not serializer.is_valid():
            logger.warning("Rejected report from server %s: %s", server.pk, serializer.errors)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = ingest_report(server, serializer.validated_data)
        return Response(
            {
                "status": "ok",
                "server_id": server.pk,
                "snapshot_id": result.snapshot_id,
                "services_saved": result.services_saved,
                "logs_saved": result.logs_saved,
                "server_time": timezone.now().isoformat(),
            },
            status=status.HTTP_201_CREATED,
        )
