"""One error envelope for every endpoint: {"message": str, "errors": {...}}."""
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    detail = response.data
    message = detail.get("detail") if isinstance(detail, dict) else None
    if message is None:
        response.data = {"message": "请求未被接受。", "errors": detail}
    else:
        response.data = {"message": str(message), "errors": {}}
    return response
