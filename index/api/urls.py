"""REST v1 routes. Registered here so index.urls stays a page-only map."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from index.api.viewsets import MaintenanceWindowViewSet

router = DefaultRouter()
router.register("maintenance-windows", MaintenanceWindowViewSet, basename="maintenance-window")

urlpatterns = [
    path("", include(router.urls)),
]
