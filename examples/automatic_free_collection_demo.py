"""Executable synthetic automatic-collection demo; no network or private capture."""
import json

import httpx

from keywordmoves.builtin.ytkeywordtool import YTKeywordToolPlugin
from keywordmoves.models import ExecutionContext, PluginRequest


def response(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /")
    return httpx.Response(200, json={"input": "synthetic song", "seed": "synthetic song",
        "count": 1, "keywords": [{"term": "synthetic song lyrics", "tier": "High",
        "category": "Related", "score": 3.5}]})


if __name__ == "__main__":
    # The synthetic clock avoids real pacing delays; production retains them.
    from unittest.mock import patch
    with patch("keywordmoves.online.common.time.sleep"):
        result = YTKeywordToolPlugin(transport=httpx.MockTransport(response)).run(
            PluginRequest("collect-public", ("synthetic song",)), ExecutionContext(None))
    assert result.metadata["network_requests"] == 2
    assert result.keywords[0].score is None
    print(json.dumps({"synthetic": True, "actual_network_calls": 0, "result": result.to_dict()}, indent=2))
