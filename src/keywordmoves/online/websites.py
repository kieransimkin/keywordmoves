"""Explicit, low-rate public-web access; no cookies, login automation or challenge bypass."""
from __future__ import annotations

import csv
import io
import ipaddress
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from ..errors import ConfigurationError, InputError
from ..models import PluginDescriptor, PluginResult
from .common import (
    HTTP,
    USER_AGENT,
    OnlinePlugin,
    OnlineSourceError,
    array,
    boolean,
    candidate,
    choice,
    code,
    integer,
    iso_date,
    number,
    seeds,
    text,
)


def public_url(url: str) -> str:
    """Reject non-public destinations before a user-configured website request.

    This is a local CLI safeguard, not a hardened server-side URL proxy: deployments
    accepting untrusted URLs also need network-level egress controls against DNS rebinding.
    """
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            raise ValueError
        host = parsed.hostname
        if "." not in host or host.endswith((".local", ".internal", ".localhost")):
            raise ValueError
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise ConfigurationError("Website URLs must resolve to public HTTPS destinations without credentials or fragments.") from None
    return host


def check_robots(http: HTTP, url: str) -> None:
    parsed = urlsplit(url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    status, body = http.request("GET", robots_url, allowed_statuses=(404, 410))
    if status in (404, 410):
        return
    # A CAPTCHA or HTML error page must not be interpreted as an empty allow-all file.
    if "<html" in body.lower() or "<!doctype html" in body.lower():
        raise OnlineSourceError("The robots policy could not be read reliably; website access stopped.")
    parser = RobotFileParser()
    parser.parse(body.splitlines())
    if not parser.can_fetch(USER_AGENT, url):
        raise OnlineSourceError("The website's robots policy disallows this request; no override is provided.")
    delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*")
    rate = parser.request_rate(USER_AGENT) or parser.request_rate("*")
    if rate and rate.requests:
        delay = max(delay or 0, rate.seconds / rate.requests)
    if delay:
        if delay > 60:
            raise OnlineSourceError("The website requests a crawl delay over 60 seconds; use an export instead.")
        http.interval = max(http.interval, delay)


class WebAutocompletePlugin(OnlinePlugin):
    access = "unofficial-web-endpoint"
    interval = 2.0
    endpoint = ""

    def fetch(self, request, http, observed):
        o = request.options
        if not boolean(o, "allow_unofficial"):
            raise ConfigurationError("This is an unsupported website-client endpoint. Set allow_unofficial=true after reviewing provider rules.")
        query = seeds(request)[0]
        if self.descriptor.name == "google-autocomplete":
            params = {"client": "firefox", "q": query, "hl": code(o, "language", "en")}
        elif self.descriptor.name == "bing-autocomplete":
            params = {"query": query}
        else:
            params = {"q": query, "type": "list"}
        # Include the actual query when evaluating robots rules.
        from urllib.parse import urlencode
        check_robots(http, self.endpoint + "?" + urlencode(params))
        data = array(http.json("GET", self.endpoint, params=params))
        if len(data) < 2 or not isinstance(data[0], str):
            raise OnlineSourceError("The website autocomplete response schema changed.")
        results = array(data[1])
        items = [candidate(self.descriptor.name, value, {"suggestion_order": (index, "ordinal")},
                           observed_at=observed, note="Experimental website autocomplete; not a supported keyword-volume API.")
                 for index, value in enumerate(results, 1)]
        return items, {"language": params.get("hl"), "country": None}, [
            "Endpoint identified in public browser source, not an official developer contract. It may stop working without notice.",
            "No region-specific demand inference; there is no automatic fallback after blocking or format changes.",
        ]


class GoogleAutocompletePlugin(WebAutocompletePlugin):
    descriptor = PluginDescriptor("google-autocomplete", "Experimental Google browser suggestions; explicit opt-in required.",
                                  ("discover", "experimental-web"), ("suggestions",))
    hosts = ("suggestqueries.google.com",)
    endpoint = "https://suggestqueries.google.com/complete/search"


class BingAutocompletePlugin(WebAutocompletePlugin):
    descriptor = PluginDescriptor("bing-autocomplete", "Experimental Bing browser suggestions; explicit opt-in required.",
                                  ("discover", "experimental-web"), ("suggestions",))
    hosts = ("api.bing.com",)
    endpoint = "https://api.bing.com/osjson.aspx"


class DuckDuckGoAutocompletePlugin(WebAutocompletePlugin):
    descriptor = PluginDescriptor("duckduckgo-autocomplete", "Experimental DuckDuckGo browser suggestions; explicit opt-in required.",
                                  ("discover", "experimental-web"), ("suggestions",))
    hosts = ("ac.duckduckgo.com",)
    endpoint = "https://ac.duckduckgo.com/ac/"


def read_file(request) -> str:
    if len(request.inputs) != 1:
        raise ConfigurationError("Supply exactly one --input file for this import operation.")
    path = Path(request.inputs[0])
    maximum = integer(request.options, "max_response_bytes", 2_000_000, 1024, 10_000_000)
    try:
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise InputError("Input file exceeds max_response_bytes.")
        return data.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        raise InputError("The input file must be a readable UTF-8 CSV or HTML file.") from None


def parse_html(body: str, options, source: str, observed: str, source_url: str | None):
    try:
        from bs4 import BeautifulSoup
        from soupsieve.util import SelectorSyntaxError
    except ImportError:
        raise ConfigurationError("HTML extraction needs: pip install 'keywordmoves[online]'.") from None
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    challenge = soup.select_one(".g-recaptcha, .h-captcha, #challenge-form, input[type=password]")
    if challenge or any(token in title for token in ("access denied", "verify you are", "just a moment", "sign in", "log in", "captcha")):
        raise OnlineSourceError("The page is a login/access challenge, not keyword results; extraction stopped.")
    for tag in soup.select("script, style, template"):
        tag.decompose()
    selector = text(options, "selector")
    try:
        nodes = soup.select(selector)
        empty = soup.select(text(options, "empty_selector")) if "empty_selector" in options else []
    except SelectorSyntaxError:
        raise ConfigurationError("The configured CSS selector is invalid.") from None
    if not nodes:
        if empty:
            return [], ["The explicitly configured no-results marker was present."]
        raise OnlineSourceError("No keyword elements matched. The layout may have changed or require JavaScript; this is not a zero-result claim.")
    words, seen = [], set()
    for node in nodes:
        # Do not collect content from explicitly hidden DOM elements.
        if any(parent.has_attr("hidden") or parent.get("aria-hidden") == "true" or
               "display:none" in parent.get("style", "").replace(" ", "").lower()
               for parent in [node, *node.parents] if getattr(parent, "attrs", None) is not None):
            continue
        word = " ".join(node.stripped_strings)
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            words.append(word)
    if not words:
        raise OnlineSourceError("Matched elements contained no visible keyword text.")
    items = [candidate(source, word, {"returned_order": (i, "ordinal")}, observed_at=observed,
                       geography=options.get("country"), metadata={"source_url": source_url},
                       note="Text extracted using an explicitly selected DOM rule; no volume is inferred.")
             for i, word in enumerate(words, 1)]
    return items, []


class WebsiteKeywordsPlugin(OnlinePlugin):
    descriptor = PluginDescriptor("website-keywords", "Public HTML query/extraction with reviewed URL and CSS rules; or saved DOM import.",
                                  ("discover", "public-html", "import"), ("query", "import-html"))
    access = "public-html"

    def run(self, request, context):
        if request.operation == "import-html":
            observed = iso_date(request.options, "observed_at")
            items, notes = parse_html(read_file(request), request.options, "Saved website HTML", observed,
                                      request.options.get("source_url"))
            limit = integer(request.options, "limit", 50, 1, 1000)
            return PluginResult(self.descriptor.name, request.operation, tuple(items[:limit]), tuple(notes),
                                {"access": "saved-html", "output_truncated": len(items) > limit,
                                 "live_query_performed": False, "source_file": str(request.inputs[0])})
        return super().run(request, context)

    def _url(self, request):
        template = text(request.options, "url_template")
        if template.count("{query}") != 1 or "{query}" not in urlsplit(template).query:
            raise ConfigurationError("url_template must have one {query} placeholder in its query string, not host or path.")
        result = template.replace("{query}", quote_plus(seeds(request)[0]))
        if "{" in result or "}" in result:
            raise ConfigurationError("Unexpected template placeholder.")
        return result

    def hosts_for(self, request):
        return (public_url(self._url(request)),)

    def fetch(self, request, http, observed):
        url = self._url(request)
        # Validate the selector before making any network request.
        text(request.options, "selector")
        check_robots(http, url)
        public_url(url)
        _, body = http.request("GET", url)
        items, notes = parse_html(body, request.options, "Public website HTML", observed, url)
        return items, {"source_url": url}, notes + [
            "Static public HTML only. No login, JavaScript execution, cookies supplied by the user, or CAPTCHA bypass.",
            "The URL template and selectors are user-reviewed configuration, not verified built-in recipes for every website.",
        ]


class ExportPlugin:
    """Honest report adapters for tools without a verified public live-query contract."""
    source = ""

    def run(self, request, context):
        del context
        if request.operation not in self.descriptor.operations:
            raise ConfigurationError(f"{self.descriptor.name} supports import-csv and import-html, not live website queries.")
        if request.keywords:
            raise ConfigurationError("Report import operations do not query or filter --keyword seeds.")
        o = request.options
        observed = iso_date(o, "observed_at")
        platform = text(o, "platform")
        limit = integer(o, "limit", 50, 1, 1000)
        body = read_file(request)
        if request.operation == "import-html":
            items, notes = parse_html(body, o, self.source + " saved HTML", observed, o.get("source_url"))
        else:
            items, notes = self._csv(body, o, observed, platform)
        return PluginResult(self.descriptor.name, request.operation, tuple(items[:limit]), tuple(notes),
                            {"access": "user-export", "live_query_performed": False,
                             "platform": platform, "source_file": str(request.inputs[0]),
                             "output_truncated": len(items) > limit,
                             "retrieved_at": datetime.now(timezone.utc).isoformat()})

    def _csv(self, body, o, observed, platform):
        delimiter = choice(o, "delimiter", ",", (",", ";", "tab"))
        reader = csv.DictReader(io.StringIO(body), delimiter="\t" if delimiter == "tab" else delimiter)
        phrase_column = text(o, "phrase_column")
        maps = {"volume_column": ("reported_search_volume", "searches_per_month"),
                "difficulty_column": ("reported_organic_difficulty", "provider_index"),
                "cpc_column": ("reported_cpc", text(o, "currency") if "cpc_column" in o else "")}
        selected = {text(o, key): (metric, unit) for key, (metric, unit) in maps.items() if key in o}
        if not {phrase_column, *selected}.issubset(set(reader.fieldnames or [])):
            raise InputError("Configured export column names are missing; verify the delimiter and exact headings.")
        items = []
        for index, row in enumerate(reader, 1):
            if None in row or any(value is None for value in row.values()):
                raise InputError("The exported CSV contains inconsistent column counts.")
            if not any(row.values()):
                continue
            values = {"export_order": (index, "ordinal")}
            for column, (metric, unit) in selected.items():
                raw = row[column].strip()
                if raw.casefold() in {"", "n/a", "null", "-"}:
                    value = None
                else:
                    if "," in raw and not re.fullmatch(r"[+-]?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?", raw):
                        raise InputError("Ambiguous numeric locale in export; use dot decimals and valid comma thousands groups.")
                    value = number(raw.replace(",", ""))
                values[metric] = (value, unit)
            items.append(candidate(self.source + " user export", row[phrase_column], values,
                                   observed_at=observed, geography=o.get("country"),
                                   metadata={"platform": platform},
                                   note="User-supplied report with explicit column mapping, not a live verified API response."))
        return items, ["Column meanings, units, locale and observation date must match the report you exported."]


class UbersuggestPlugin(ExportPlugin):
    source = "Ubersuggest"
    descriptor = PluginDescriptor("ubersuggest", "Import Ubersuggest CSV or rendered HTML; no public API/live scraper is verified.",
                                  ("import", "user-export"), ("import-csv", "import-html"))


class AnswerThePublicPlugin(ExportPlugin):
    source = "AnswerThePublic"
    descriptor = PluginDescriptor("answerthepublic", "Import AnswerThePublic CSV or rendered HTML with explicit extraction rules.",
                                  ("import", "user-export"), ("import-csv", "import-html"))
