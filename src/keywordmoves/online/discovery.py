"""Official APIs for question discovery, autocomplete and related language."""
from __future__ import annotations

from ..models import PluginDescriptor
from .common import (
    OnlinePlugin,
    OnlineSourceError,
    array,
    boolean,
    candidate,
    code,
    integer,
    number,
    obj,
    phrase,
    provider_error,
    secret,
    seeds,
)


class AlsoAskedPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("alsoasked", "Discover nested People Also Ask questions through AlsoAsked's API.",
                                  ("discover", "questions", "official-api"), ("questions",))
    hosts = ("alsoaskedapi.com", "sandbox.alsoaskedapi.com")

    def fetch(self, request, http, observed):
        o = request.options
        terms = seeds(request, 5)
        region = code(o, "country").lower()
        language = code(o, "language", "en").lower()
        body = {"terms": terms, "region": region, "language": language,
                "depth": integer(o, "depth", 2, 2, 3), "fresh": boolean(o, "fresh"),
                "async": False, "notify_webhooks": False}
        host = self.hosts[1] if boolean(o, "sandbox") else self.hosts[0]
        data = provider_error(http.json("POST", f"https://{host}/v1/search", json=body,
                                        headers={"X-Api-Key": secret(o, "api_key", "ALSOASKED_API_KEY")}))
        if data.get("status") != "success":
            raise OnlineSourceError("AlsoAsked search is not complete/successful. No retry, resubmission or webhook was triggered.")
        items = []
        for query in array(data.get("queries")):
            query = obj(query)
            term = phrase(query.get("term"))
            pending = [(row, term, 1) for row in reversed(array(query.get("results")))]
            while pending:
                row, parent, depth = pending.pop()
                row = obj(row)
                if depth > 10 or len(items) >= 10000:
                    raise OnlineSourceError("AlsoAsked returned an unexpectedly large or deep question tree.")
                question = phrase(row.get("question"))
                items.append(candidate("AlsoAsked", question, {"question_depth": (depth, "tree_level")},
                                       observed_at=observed, geography=region, relationship="related-question",
                                       metadata={"seed": term, "parent_question": parent, "language": language},
                                       note="A People Also Ask relationship, not search volume or popularity."))
                pending.extend((child, question, depth + 1) for child in reversed(array(row.get("results", []))))
        return items, {"country": region, "language": language, "sandbox": boolean(o, "sandbox")}, [
            "Synchronous search only; no webhooks or background polling. Search depth affects credit use.",
        ]


class SerpAPIPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("serpapi", "Google autocomplete, related searches and questions through SerpApi.",
                                  ("discover", "questions", "official-api"), ("autocomplete", "related-searches", "questions"))
    hosts = ("serpapi.com",)

    def fetch(self, request, http, observed):
        o = request.options
        seed = seeds(request)[0]
        country = code(o, "country").lower()
        language = code(o, "language", "en")
        autocomplete = request.operation == "autocomplete"
        params = {"engine": "google_autocomplete" if autocomplete else "google", "q": seed,
                  "gl": country, "hl": language, "api_key": secret(o, "api_key", "SERPAPI_API_KEY")}
        data = provider_error(http.json("GET", "https://serpapi.com/search.json", params=params))
        key, field = {"autocomplete": ("suggestions", "value"),
                      "related-searches": ("related_searches", "query"),
                      "questions": ("related_questions", "question")}[request.operation]
        # Google results may legitimately omit a related-search/question block. For the
        # autocomplete endpoint a missing suggestions field is not an empty result.
        if autocomplete and key not in data:
            raise OnlineSourceError("SerpApi returned no suggestions field; the response schema may have changed.")
        if obj(data.get("search_metadata") or {}).get("status") != "Success":
            raise OnlineSourceError("SerpApi did not confirm a successful search.")
        items = []
        for index, row in enumerate(array(data.get(key, [])), 1):
            row = obj(row)
            items.append(candidate("SerpApi Google", row.get(field), {"returned_order": (index, "ordinal")},
                                   observed_at=observed, geography=country, relationship=request.operation,
                                   metadata={"language": language, "seed": seed},
                                   note="Ordering of a returned search feature, not search volume or organic rank."))
        return items, {"engine": params["engine"], "country": country, "language": language}, [
            "SerpApi is a third-party search-data API, not Google's first-party keyword planning service.",
        ]


class BraveSuggestPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("brave-suggest", "Brave's documented search suggestion API.",
                                  ("discover", "official-api"), ("suggestions",))
    hosts = ("api.search.brave.com",)

    def fetch(self, request, http, observed):
        o = request.options
        seed = seeds(request)[0]
        if len(seed.split()) > 50:
            from .common import ConfigurationError
            raise ConfigurationError("Brave queries are limited to 50 words.")
        country = code(o, "country").upper()
        language = code(o, "language", "en")
        params = {"q": seed, "country": country, "lang": language,
                  "count": min(integer(o, "limit", 50, 1, 1000), 20)}
        data = provider_error(http.json("GET", "https://api.search.brave.com/res/v1/suggest/search",
                                        params=params, headers={"X-Subscription-Token": secret(o, "api_key", "BRAVE_SEARCH_API_KEY")}))
        items = [candidate("Brave Suggest API", obj(row).get("query"), {"suggestion_order": (i, "ordinal")},
                           observed_at=observed, geography=country,
                           note="Autocomplete order is not a popularity measure.")
                 for i, row in enumerate(array(data.get("results")), 1)]
        return items, {"country": country, "language": language}, [
            "Check your Brave plan's storage rights before retaining results in a permanent register.",
        ]


class DatamusePlugin(OnlinePlugin):
    descriptor = PluginDescriptor("datamuse", "Datamuse semantic associations, synonyms and word suggestions.",
                                  ("discover", "related-language", "official-api"), ("related", "synonyms", "suggestions"))
    hosts = ("api.datamuse.com",)

    def fetch(self, request, http, observed):
        seed = seeds(request)[0]
        operation = request.operation
        parameter = {"related": "ml", "synonyms": "rel_syn", "suggestions": "s"}[operation]
        route = "sug" if operation == "suggestions" else "words"
        data = http.json("GET", f"https://api.datamuse.com/{route}",
                         params={parameter: seed, "max": integer(request.options, "limit", 50, 1, 1000)})
        items = [candidate("Datamuse", obj(row).get("word"),
                           {"lexical_match_score": (number(row.get("score")), "provider_rank_score")},
                           observed_at=observed, relationship=operation,
                           note="Lexical association score with no search-volume interpretation.") for row in array(data)]
        return items, {"language": "English"}, [
            "Related language is not search demand. Attribute Datamuse when displaying its data publicly.",
            "Datamuse announces API-key requirements from 1 January 2027; this unauthenticated adapter may need updating then.",
        ]


class WikipediaPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("wikipedia", "MediaWiki OpenSearch topic/title suggestions (not search-volume data).",
                                  ("discover", "topics", "official-api"), ("suggestions",))

    def hosts_for(self, request):
        language = code(request.options, "language", "en", r"[a-z]{2,3}(-[a-z]+)?")
        return (language + ".wikipedia.org",)

    def fetch(self, request, http, observed):
        host = self.hosts_for(request)[0]
        data = array(http.json("GET", f"https://{host}/w/api.php", params={
            "action": "opensearch", "search": seeds(request)[0], "format": "json", "namespace": 0,
            "limit": min(integer(request.options, "limit", 50, 1, 1000), 500),
        }))
        if len(data) != 4:
            raise OnlineSourceError("Wikipedia returned an unexpected OpenSearch response.")
        titles, descriptions, urls = array(data[1]), array(data[2]), array(data[3])
        if not len(titles) == len(descriptions) == len(urls):
            raise OnlineSourceError("Wikipedia OpenSearch result columns have inconsistent lengths.")
        items = [candidate("Wikipedia OpenSearch", word, {"suggestion_order": (i + 1, "ordinal")},
                           observed_at=observed, relationship="related-topic",
                           metadata={"page_url": urls[i]}, note="Encyclopaedia title suggestion, not keyword demand.")
                 for i, word in enumerate(titles)]
        return items, {"language": host.split(".")[0]}, []
