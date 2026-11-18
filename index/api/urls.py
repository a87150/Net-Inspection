"""REST v1 routes. Registered here so index.urls stays a page-only map."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from index.api.viewsets import (
    AlertEventViewSet, ErrorRecordViewSet, InspectionRecordViewSet,
    IpSubnetViewSet, MaintenanceWindowViewSet,
)

router = DefaultRouter()
router.register("maintenance-windows", MaintenanceWindowViewSet, basename="maintenance-window")
router.register("ip-subnets", IpSubnetViewSet, basename="ip-subnet")
router.register("alert-events", AlertEventViewSet, basename="alert-event")
router.register("inspection-records", InspectionRecordViewSet, basename="inspection-record")
router.register("error-records", ErrorRecordViewSet, basename="error-record")

urlpatterns = [
    path("", include(router.urls)),
]
