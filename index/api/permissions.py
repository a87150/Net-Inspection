"""Permission mapping for the REST layer.

The HTML views rely on AccessMiddleware; DRF bypasses that whitelist on purpose
(see access.AccessMiddleware) so each endpoint states its own rule here.
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from index.common.access import is_admin


class IsReader(BasePermission):
    message = "需要登录后才能访问。"

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.is_active)


class IsAdminOrReadOnly(IsReader):
    message = "只有管理员可以执行这个操作。"

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        return request.method in SAFE_METHODS or is_admin(request.user)
