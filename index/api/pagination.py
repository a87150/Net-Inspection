"""Pagination that snaps to the same page sizes as the server-rendered tables."""
from rest_framework.pagination import PageNumberPagination

from index.common.table_query import PAGE_SIZES


class StandardPagination(PageNumberPagination):
    page_size = PAGE_SIZES[0]
    page_size_query_param = "page_size"
    max_page_size = PAGE_SIZES[-1]

    def get_page_size(self, request):
        raw = request.query_params.get(self.page_size_query_param)
        if not raw:
            return self.page_size
        try:
            requested = int(raw)
        except (TypeError, ValueError):
            return self.page_size
        if requested in PAGE_SIZES:
            return requested
        if requested <= PAGE_SIZES[0]:
            return PAGE_SIZES[0]
        if requested >= PAGE_SIZES[-1]:
            return PAGE_SIZES[-1]
        return max(size for size in PAGE_SIZES if size < requested)
