"""Bing Search keyword module. See docs/bing-search.md for access and metric scope."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..errors import ConfigurationError
from ..models import ExecutionContext, PluginDescriptor, PluginRequest, PluginResult
from . import bing_search_analysis as analysis
from . import bing_search_imports as imports
from . import bing_search_providers as providers
from . import bing_webmaster as webmaster
from .common import HTTP, boolean, integer

_NETWORK = {"timeout", "max_requests", "max_response_bytes", "min_interval"}
_OUTPUT = {"limit", "sort_by"}
_INPUT = {"max_input_bytes"}
_TARGET = {"top_n", "target_host", "include_subdomains"}
_REVIEW = {"min_impressions", "min_position", "max_position", "max_ctr", "target_ctr"}
_SERPAPI = {"api_key", "market", "device", "first", "pages", "location", "safe_search", "filters", "no_cache"}
_DFS = {"login", "password", "location_code", "language"}
_KT = {"api_key", "country", "language", "currency", "location_code", "network", "metrics_source",
       "sandbox", "metrics", "suggestion_type", "category"}
_CSV_BWT = {"source", "observed_at", "site_url", "scope", "report_type", "start_date", "end_date",
            "period_totals", "query_column", "page_column", "clicks_column", "impressions_column",
            "impression_position_column", "click_position_column", "ctr_column", "ctr_is_percent",
            "delimiter", "input_format", "query", "page_url"}
_CSV_OBS = {"source", "scope", "observed_at", "geography", "period_start", "period_end", "metric",
            "unit", "phrase_column", "value_column", "metric_column", "unit_column", "delimiter", "approximate"}
_HTML = {"source", "observed_at", "organic_only", "market", "device", "location", "safe_search",
         "filters", "first", "row_selector", "link_selector", "title_selector", "snippet_selector", "no_results_selector"}
_LOCAL = ("bwt-import", "bwt-compare", "import-serp", "import-serp-html", "serp-compare",
          "import-observations", "compare", "combine", "keyword-gap")
_SERP = ("serp", "competition", "rank-check", "related", "questions")


class BingSearchPlugin:
    descriptor = PluginDescriptor(
        "bing-search", "Bing Webmaster, suggestions, demand and bounded organic competition analysis.",
        ("discover", "analyse", "property-performance", "organic-competition", "import", "compare"),
        (*webmaster.METHODS, "bwt-opportunities", "bwt-overlap", *_SERP, "autocomplete",
         "suggestions", "metrics", "ideas", "url-ideas", "competitor-keywords", *_LOCAL),
    )

    def __init__(self, *, transport: Any = None) -> None:
        self.transport = transport

    def _allowed(self, request: PluginRequest) -> set[str]:
        op, o = request.operation, request.options
        if op in webmaster.METHODS:
            _, kind, req = webmaster.METHODS[op]
            extra = (set(req) - {"query", "q", "period"}) | webmaster.AUTH_OPTIONS
            if "period" in req:
                extra |= {"start_date", "end_date"}
            if kind in {"links", "url-links"}:
                extra |= {"pages", "page_index"}
            return _NETWORK | extra
        if op in {"bwt-opportunities", "bwt-overlap"}:
            return (_REVIEW if op == "bwt-opportunities" else set()) | (
                _INPUT if request.inputs else webmaster.AUTH_OPTIONS | _NETWORK | {"site_url"})
        if op == "bwt-import":
            return _INPUT | _CSV_BWT
        if op in _LOCAL:
            extras = _CSV_OBS if op == "import-observations" else _HTML if op == "import-serp-html" else set()
            return _INPUT | extras | (_TARGET if op in {"import-serp", "import-serp-html"} else set())
        if op in _SERP:
            provider = o.get("provider", "serpapi")
            if provider not in {"serpapi", "dataforseo"}:
                raise ConfigurationError("SERP provider must be serpapi or dataforseo; retired Bing Search APIs are not used.")
            return _NETWORK | {"provider"} | _TARGET | (_SERPAPI if provider == "serpapi" else _DFS | {"device", "depth"})
        if op == "autocomplete":
            return _NETWORK | {"allow_unofficial", "market", "expand", "probe_offset", "max_queries"}
        provider = o.get("provider", "keywordtool" if op == "suggestions" else "dataforseo")
        if op == "suggestions" and provider != "keywordtool":
            raise ConfigurationError("suggestions uses provider=keywordtool; use autocomplete for the experimental browser route.")
        if op in {"ideas", "url-ideas", "competitor-keywords"} and provider != "dataforseo":
            raise ConfigurationError("This operation requires provider=dataforseo.")
        if provider not in {"keywordtool", "dataforseo"}:
            raise ConfigurationError("Provider must be keywordtool or dataforseo.")
        if provider == "keywordtool":
            return _NETWORK | {"provider"} | _KT
        extra = {"device", "search_partners"}
        if op == "url-ideas":
            return _NETWORK | {"provider", "login", "password", "target", "language", "exclude_brands"}
        if op == "competitor-keywords":
            extra = {"target", "offset"}
        return _NETWORK | {"provider"} | _DFS | extra

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        op, o = request.operation, request.options
        if op not in self.descriptor.operations:
            raise ConfigurationError("Unsupported bing-search operation; see docs/bing-search.md.")
        unknown = set(o) - (self._allowed(request) | _OUTPUT)
        if unknown:
            # Never echo option values, which might include credentials.
            raise ConfigurationError("Unknown or inapplicable Bing options: " + ", ".join(sorted(unknown)))
        integer(o, "limit", 100, 1, 50000)
        if "sort_by" in o and (not isinstance(o["sort_by"], str) or not o["sort_by"].strip()):
            raise ConfigurationError("sort_by must be an evidence metric name.")
        if "top_n" in o:
            integer(o, "top_n", 10, 1, 100)
        if "target_host" in o:
            analysis.target_host(o["target_host"])
        if "include_subdomains" in o:
            boolean(o, "include_subdomains")
        if op == "bwt-opportunities":
            analysis.bwt_opportunities({"rows": []}, o)  # Validate thresholds before a read.
        if op == "rank-check" and "target_host" not in o:
            raise ConfigurationError("rank-check requires target_host.")
        local = op in _LOCAL or (op in {"bwt-opportunities", "bwt-overlap"} and bool(request.inputs))
        if not local and request.inputs:
            raise ConfigurationError("Live Bing operations do not accept --input; use an explicit import operation.")
        if local:
            if request.keywords and op != "import-serp-html":
                raise ConfigurationError("This local operation reads keywords from its inputs, not --keyword.")
            items, metadata, notes = self._local(request)
            metadata["live_query_performed"] = False
        else:
            if op in {"url-ideas", "competitor-keywords"} and request.keywords:
                raise ConfigurationError("This operation uses target=..., not --keyword.")
            observed = datetime.now(timezone.utc).isoformat()
            hosts = self._hosts(request)
            interval = 4.0 if "api.keywordtool.io" in hosts else 2.0 if op == "autocomplete" else 1.0
            with HTTP(o, hosts, transport=self.transport, interval=interval) as http:
                items, metadata, notes = self._live(request, http, observed)
                metadata.update(live_query_performed=True, requests_made=http.requests,
                                request_budget=http.maximum, captured_at=observed)
        limit = integer(o, "limit", 100, 1, 50000)
        if "sort_by" in o:
            key = o["sort_by"]
            if not isinstance(key, str) or (items and not any(e.metric == key for x in items for e in x.evidence)):
                raise ConfigurationError("sort_by must name an available numeric evidence metric.")
            def sort_value(item):
                v = next((e.value for e in item.evidence if e.metric == key), None)
                return (not isinstance(v, (int, float)), -v if isinstance(v, (int, float)) else 0, item.phrase)
            items.sort(key=sort_value)
        if "records" in metadata:
            metadata["records_received"] = len(metadata["records"])
            metadata["records_truncated"] = len(metadata["records"]) > limit
            metadata["records"] = metadata["records"][:limit]
        return PluginResult("bing-search", op, tuple(items[:limit]), tuple(notes),
                            {**metadata, "platform": "Bing Search", "candidate_count": len(items),
                             "output_truncated": len(items) > limit})

    def _hosts(self, request):
        op, o = request.operation, request.options
        if op.startswith("bwt-"):
            return ("ssl.bing.com", "www.bing.com")
        if op == "autocomplete":
            return ("api.bing.com",)
        default = "serpapi" if op in _SERP else "keywordtool" if op == "suggestions" else "dataforseo"
        return {"serpapi": ("serpapi.com",), "dataforseo": ("api.dataforseo.com",),
                "keywordtool": ("api.keywordtool.io",)}[o.get("provider", default)]

    def _live(self, request, http, observed):
        op, o = request.operation, request.options
        if op in webmaster.METHODS:
            return webmaster.fetch(request, http, observed)
        if op in {"bwt-opportunities", "bwt-overlap"}:
            settings = {k: v for k, v in o.items() if k in webmaster.AUTH_OPTIONS | {"site_url"}}
            source = PluginRequest("bwt-queries" if op == "bwt-opportunities" else "bwt-query-pages",
                                   keywords=request.keywords, options=settings)
            _, meta, notes = webmaster.fetch(source, http, observed)
            report = meta["report"]
            if op == "bwt-overlap":
                items, extra = analysis.bwt_overlap(report)
                meta.update(extra)
            else:
                items = analysis.bwt_opportunities(report, o)
            return items, meta, notes
        if op in _SERP:
            if o.get("provider", "serpapi") == "serpapi":
                snapshot, meta = providers.serpapi(request, http, observed), {}
            else:
                snapshot, meta = providers.dataforseo_serp(request, http, observed)
            items = [analysis.competition(snapshot, o)]
            suggestions = analysis.serp_candidates(snapshot)
            if op == "serp":
                items.extend(suggestions)
            elif op in {"related", "questions"}:
                wanted = "related-search" if op == "related" else "related-question"
                items = [x for x in suggestions if x.relationship == wanted]
            return items, {**meta, "snapshot": snapshot}, [analysis.SERP_NOTE]
        if op == "autocomplete":
            return providers.autocomplete(request, http, observed)
        if o.get("provider", "keywordtool" if op == "suggestions" else "dataforseo") == "keywordtool":
            return providers.keywordtool(request, http, observed)
        return providers.dataforseo_keywords(request, http, observed)

    def _local(self, request):
        op, o = request.operation, request.options
        required = 2 if op in {"bwt-compare", "serp-compare", "compare", "keyword-gap"} else 1
        if op == "combine":
            if not 1 <= len(request.inputs) <= 20:
                raise ConfigurationError("combine requires between 1 and 20 input files.")
        elif len(request.inputs) != required:
            raise ConfigurationError(f"{op} requires exactly {required} input file(s).")
        paths = list(request.inputs)
        if op == "bwt-import":
            report = imports.bwt_import(paths[0], o)
            return analysis.bwt_candidates(report), {"report": report}, [analysis.BWT_NOTE]
        if op in {"bwt-opportunities", "bwt-overlap", "bwt-compare"}:
            reports = [analysis.validate_bwt(imports.unwrap(imports.read_json(p, o), "report")) for p in paths]
            if op == "bwt-compare":
                items, meta = analysis.compare_bwt(*reports)
                return items, meta, [analysis.BWT_NOTE]
            report = reports[0]
            if op == "bwt-overlap":
                items, extra = analysis.bwt_overlap(report)
            else:
                items, extra = analysis.bwt_opportunities(report, o), {}
            return items, {"report": report, **extra}, [analysis.BWT_NOTE]
        if op in {"import-serp", "import-serp-html", "serp-compare"}:
            snapshots = [imports.serp_html(paths[0], request)] if op == "import-serp-html" else [
                analysis.validate_serp(imports.unwrap(imports.read_json(p, o), "snapshot")) for p in paths]
            if op == "serp-compare":
                items, meta = analysis.compare_serps(*snapshots)
                return items, meta, [analysis.SERP_NOTE]
            snapshot = snapshots[0]
            return [analysis.competition(snapshot, o), *analysis.serp_candidates(snapshot)], {"snapshot": snapshot}, [analysis.SERP_NOTE]
        if op in {"import-observations", "compare"}:
            reports = [imports.observation_import(p, o) for p in paths]
            if op == "compare":
                items, meta = analysis.compare_observations(*reports)
                return items, meta, ["Different scopes, periods and approximate observations are not numerically compared."]
            return analysis.observation_candidates(reports[0]), {"report": reports[0]}, []
        items, meta = analysis.combine_reports([imports.read_json(p, o) for p in paths], gap=op == "keyword-gap")
        return items, meta, ["Absence from an input list is not proof of absent demand or rankings."]
