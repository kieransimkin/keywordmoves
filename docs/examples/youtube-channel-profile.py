"""Offline example: one channel identity request, no upload collection or live token."""
import json

import httpx

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.youtube import YouTubePlugin


def response(request):
    assert request.url.path == "/youtube/v3/channels"
    assert request.url.params["mine"] == "true"
    return httpx.Response(200, json={"items": [{
        "id": "UC" + "a" * 22,
        "snippet": {"title": "Example music channel", "description": "Synthetic profile"},
        "statistics": {"hiddenSubscriberCount": True},
    }]})


result = YouTubePlugin(transport=httpx.MockTransport(response)).run(
    PluginRequest("channel", options={"mine": True, "include_uploads": False,
                                     "max_requests": 1, "access_token": "example-only"}),
    ExecutionContext(None),
)
assert result.metadata["request_count"] == 1
assert result.metadata["endpoint_calls"] == {"channels": 1}
assert not result.keywords
print(json.dumps(result.to_dict(), indent=2))
