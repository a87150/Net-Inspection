"""Resource endpoints. One viewset per aggregate, thin over the shared services.

Rows are produced by the same payload builder the server-rendered模板 uses, so the
list response and the page's hydrated first paint cannot drift apart.
"""
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from index.api.pagination import StandardPagination
from index.api.permissions import IsAdminOrReadOnly
from index.api.serializers import IpSubnetCreateSerializer, MaintenanceWindowCreateSerializer
from assets.maintenance import (
    KIND_TARGET_TYPES,
    STATE_CHOICES,
    create_windows,
    row_payloads,
    window_metrics,
    window_queryset,
)
from net.models import IpSubnet, MaintenanceWindow


class MaintenanceWindowViewSet(mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """GET list (paginated, ?state=), GET one, POST batch create, DELETE one."""

    permission_classes = [IsAdminOrReadOnly]
    pagination_class = StandardPagination

    def get_queryset(self):
        return window_queryset(self.request.query_params.get("state", "").strip())

    def get_paginated_response(self, data):
        """Explicit counters instead of next/previous links; the client builds its own."""
        paginator = self.paginator
        return Response({
            "count": paginator.page.paginator.count,
            "page": paginator.page.number,
            "pages": paginator.page.paginator.num_pages,
            "page_size": paginator.get_page_size(self.request),
            "results": data,
        })

    def list(self, request, *args, **kwargs):
        page = self.paginate_queryset(self.get_queryset())
        return self.get_paginated_response(row_payloads(page, timezone.now()))

    def retrieve(self, request, pk=None):
        window = self.get_object()
        return Response(row_payloads([window], timezone.now())[0])

    def create(self, request, *args, **kwargs):
        serializer = MaintenanceWindowCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        created = create_windows(
            data["target_type"], [str(item) for item in data["target_ids"]],
            data["starts_at"], data["ends_at"], data.get("note", ""),
        )
        return Response(
            {"created": len(created), "ids": [str(window.pk) for window in created]},
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"])
    def meta(self, request):
        """Options for the page chrome, so the client never hardcodes them."""
        return Response({
            "states": [{"value": value, "label": label} for value, label in STATE_CHOICES],
            "target_types": [
                {"value": value, "label": label}
                for value, label in MaintenanceWindow.TargetType.choices
            ],
            "kinds": [
                {"kind": kind, "target_type": target_type}
                for kind, target_type in KIND_TARGET_TYPES.items()
            ],
            "metrics": window_metrics(),
        })


class IpSubnetViewSet(mixins.CreateModelMixin, viewsets.GenericViewSet):
    """POST one subnet from the IP page's modal; validation is the model's clean()."""

    permission_classes = [IsAdminOrReadOnly]
    serializer_class = IpSubnetCreateSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        subnet = IpSubnet(**serializer.validated_data)
        subnet.save()
        return Response(
            {"id": str(subnet.pk), "cidr": subnet.cidr},
            status=status.HTTP_201_CREATED,
        )
