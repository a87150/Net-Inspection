"""REST v1 routes. Registered here so index.urls stays a page-only map."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from index.api.viewsets import (
    AccessRecordViewSet, AlertEventViewSet, ComputerErrorViewSet,
    ComputerLogViewSet, DomainAccountViewSet, DomainComputerViewSet,
    DomainGroupViewSet, ErrorRecordViewSet, InspectionRecordViewSet,
    IpSubnetViewSet, MaintenanceWindowViewSet, TaskRunViewSet,
)

router = DefaultRouter()
router.register("maintenance-windows", MaintenanceWindowViewSet, basename="maintenance-window")
router.register("ip-subnets", IpSubnetViewSet, basename="ip-subnet")
router.register("alert-events", AlertEventViewSet, basename="alert-event")
router.register("inspection-records", InspectionRecordViewSet, basename="inspection-record")
router.register("error-records", ErrorRecordViewSet, basename="error-record")
router.register("access-records", AccessRecordViewSet, basename="access-record")
router.register("computer-logs", ComputerLogViewSet, basename="computer-log")
router.register("computer-errors", ComputerErrorViewSet, basename="computer-error")
router.register("task-runs", TaskRunViewSet, basename="task-run")
router.register("domain-accounts", DomainAccountViewSet, basename="domain-account")
router.register("domain-computers", DomainComputerViewSet, basename="domain-computer")
router.register("domain-groups", DomainGroupViewSet, basename="domain-group")

urlpatterns = [
    path("", include(router.urls)),
]
