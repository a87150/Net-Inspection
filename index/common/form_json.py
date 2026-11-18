"""写操作的 JSON 回执。

模态框用 fetch 提交时带 X-Form-Ajax: 1，视图就把「重定向回页面」换成一段
JSON；不带这个头的普通表单 POST 完全照旧，所以禁用 JS 也能用。

只有失败分支需要显式返回 JSON：成功分支继续走重定向，前端拿到的是整页
HTML，靠 Content-Type 分辨成功与失败。
"""

HEADER = "X-Form-Ajax"


def wants_json(request):
    return request.method == "POST" and request.headers.get(HEADER) == "1"


def form_errors(errors, redirect_url=""):
    """校验失败：模态框保持打开，用户输入不丢。"""
    return _json(False, "", errors, redirect_url)


def form_saved(message, redirect_url=""):
    return _json(True, message, (), redirect_url)


def _json(ok, message, errors, redirect_url):
    from django.http import JsonResponse

    return JsonResponse(
        {
            "ok": ok,
            "message": message,
            "errors": [str(error) for error in errors],
            "redirect": redirect_url,
        },
        status=200 if ok else 400,
    )
