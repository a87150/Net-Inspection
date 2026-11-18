"""Resource endpoints. One viewset per aggregate, thin over the shared services.

Rows are produced by the same payload builder the server-rendered模板 uses, so the
list response and the page's hydrated first paint cannot drift apart.
"""
from django.core.paginator import Paginator
from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from alerts.payloads import (
    alert_event_metrics, alert_event_payloads, alert_event_queryset,
)
from index.api.pagination import StandardPagination
from index.api.permissions import IsAdminOrReadOnly, IsReader
from index.api.serializers import IpSubnetCreateSerializer, MaintenanceWindowCreateSerializer
from index.common.table_payload import row_payloads as table_row_payloads
from index.common.table_query import apply_table_filters
from index.common.table_registry import get_table_definition
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


class TableRowsViewSet(viewsets.GenericViewSet):
    """One endpoint per list page, sharing the page's own filter/sort state.

    The page keeps its GET form for filtering; this endpoint serves the rows so
    the client can re-render without the server building table HTML.
    """

    permission_classes = [IsReader]
    table_key = ""
    page_param = "page"

    def get_table(self):
        raise NotImplementedError

    def list(self, request):
        definition = get_table_definition(self.table_key)
        rows, table_state = self.get_table(request, definition)
        paginator = Paginator(rows, table_state["page_size"])
        page = paginator.get_page(request.GET.get(self.page_param))
        return Response({
            "count": paginator.count,
            "page": page.number,
            "pages": paginator.num_pages,
            "page_size": table_state["page_size"],
            "start_index": page.start_index() if paginator.count else 0,
            "end_index": page.end_index() if paginator.count else 0,
            "results": self.serialize(page.object_list),
        })

    def serialize(self, object_list):
        raise NotImplementedError


class AlertEventViewSet(TableRowsViewSet):
    table_key = "alert_events"

    def get_table(self, request, definition):
        return apply_table_filters(
            request, alert_event_queryset(), definition, include_legacy_status=False,
        )

    def serialize(self, object_list):
        return alert_event_payloads(object_list)

    def list(self, request):
        response = super().list(request)
        definition = get_table_definition(self.table_key)
        events, _state = self.get_table(request, definition)
        response.data["metrics"] = alert_event_metrics(events)
        return response


class RecordTableViewSet(TableRowsViewSet):
    """Shared row shape for the field-loop pages (records, errors, ...)."""

    base_queryset = None
    badges = None

    def get_table(self, request, definition):
        return apply_table_filters(request, self.build_queryset(), definition)

    def build_queryset(self):
        raise NotImplementedError

    def serialize(self, object_list):
        definition = get_table_definition(self.table_key)
        return table_row_payloads(object_list, definition, self.badges)


class InspectionRecordViewSet(RecordTableViewSet):
    table_key = "inspection_records"

    def build_queryset(self):
        from inspections.records import _inspection_records

        return _inspection_records()

    @property
    def badges(self):
        from inspections.records import INSPECTION_RECORD_BADGES

        return INSPECTION_RECORD_BADGES


class ErrorRecordViewSet(RecordTableViewSet):
    table_key = "error_records"

    def build_queryset(self):
        from inspections.records import _error_records

        return _error_records()

    @property
    def badges(self):
        from inspections.records import ERROR_RECORD_BADGES

        return ERROR_RECORD_BADGES


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
