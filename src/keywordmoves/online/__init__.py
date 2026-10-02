"""Independent online keyword providers. All third-party imports are lazy."""
from __future__ import annotations

from typing import Any, Callable


def factories() -> dict[str, Callable[[], Any]]:
    from .commercial import (
        AhrefsPlugin,
        DataForSEOPlugin,
        KeywordsEverywherePlugin,
        KeywordToolPlugin,
        SemrushPlugin,
    )
    from .discovery import (
        AlsoAskedPlugin,
        BraveSuggestPlugin,
        DatamusePlugin,
        SerpAPIPlugin,
        WikipediaPlugin,
    )
    from .google import GoogleAdsPlugin, SearchConsolePlugin
    from .websites import (
        AnswerThePublicPlugin,
        BingAutocompletePlugin,
        DuckDuckGoAutocompletePlugin,
        GoogleAutocompletePlugin,
        UbersuggestPlugin,
        WebsiteKeywordsPlugin,
    )

    classes = (
        AhrefsPlugin, AlsoAskedPlugin, AnswerThePublicPlugin, BingAutocompletePlugin,
        BraveSuggestPlugin, DataForSEOPlugin, DatamusePlugin, DuckDuckGoAutocompletePlugin,
        GoogleAdsPlugin, GoogleAutocompletePlugin, KeywordsEverywherePlugin, KeywordToolPlugin,
        SearchConsolePlugin, SemrushPlugin, SerpAPIPlugin, UbersuggestPlugin,
        WebsiteKeywordsPlugin, WikipediaPlugin,
    )
    return {plugin.descriptor.name: plugin for plugin in classes}
