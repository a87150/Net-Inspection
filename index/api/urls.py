"""REST v1 routes. Registered here so index.urls stays a page-only map."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from index.api.viewsets import IpSubnetViewSet, MaintenanceWindowViewSet

router = DefaultRouter()
router.register("maintenance-windows", MaintenanceWindowViewSet, basename="maintenance-window")
router.register("ip-subnets", IpSubnetViewSet, basename="ip-subnet")

urlpatterns = [
    path("", include(router.urls)),
]
