"""Project-level test packages organized by feature."""

def response_body(response):
    """Return a response body once, including a cached streaming response body."""
    try:
        return response._test_response_body
    except AttributeError:
        body = b"".join(response.streaming_content) if response.streaming else response.content
        response._test_response_body = body
        return body
