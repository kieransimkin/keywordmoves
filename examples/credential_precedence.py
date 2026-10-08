"""Offline credential precedence demo using fictional environment/CLI values only."""
import os
from unittest.mock import patch

from keywordmoves.credentials import credential_context
from keywordmoves.online.common import secret

with patch.dict(os.environ, {"SEARCH_CONSOLE_ACCESS_TOKEN": "fictional-env-token"}):
    with credential_context(store="environment"):
        assert secret({}, "access_token", "SEARCH_CONSOLE_ACCESS_TOKEN") == "fictional-env-token"
        assert secret({"access_token": "fictional-explicit-token"}, "access_token",
                      "SEARCH_CONSOLE_ACCESS_TOKEN") == "fictional-explicit-token"
print("CLI/environment precedence verified with fictional values; zero network requests.")
