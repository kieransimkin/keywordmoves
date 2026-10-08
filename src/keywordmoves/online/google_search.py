"""Google Search keyword research: Search Console, SERPs, demand and review.

This plugin composes independently labelled sources. No provider is contacted at
import time, no website challenge is bypassed, and no action edits a property,
page, sitemap, ad account or search index.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from ..errors import ConfigurationError, InputError
from ..models import ExecutionContext, PluginDescriptor, PluginRequest, PluginResult
from . import google_search_analysis as analysis
from . import google_search_console as console
from . import google_search_imports as imports
from . import google_search_providers as providers
from .common import HTTP, choice, code, integer, obj, seeds, text
from .websites import check_robots, public_url

GSC_REPORT_OPERATIONS = ("gsc-query", "gsc-pages", "gsc-query-pages", "gsc-opportunities", "gsc-overlap")
GSC_INVENTORY_OPERATIONS = ("gsc-sites", "gsc-sitemaps", "gsc-inspect")
LOCAL_OPERATIONS = ("gsc-import", "gsc-compare", "gsc-bulk-sql", "import-serp", "import-serp-html", "serp-compare",
                    "import-observations", "observed-compare", "trends-import", "trends-related-import", "combine", "keyword-gap")
NETWORK_OPERATIONS = ("serp", "competition", "rank-check", "autocomplete", "questions", "suggestions", "ideas", "metrics",
                      "ads-url-ideas", "competitor-keywords", "backlinks", "trends", "custom-search", "pagespeed", "page-audit")
# Shared, documented options plus provider options supported by existing adapters.
OPTIONS = set("""
limit max_requests max_response_bytes timeout min_interval
site_url start_date end_date dimensions search_type data_state aggregation query_operator country device page appearance
filters_json page_size pages max_rows start_row access_token language min_impressions min_position max_position max_ctr target_ctr
scope source observed_at table top_n target_host provider location location_code expand expansion_offset max_queries
allow_unofficial api_key next_page_token existing_customer cx target include_subdomains backlink_status offset timeframe data_type category
seed_keyword section window normalization_id seed_type url customer_id api_version location_codes language_id developer_token login_customer_id page_token strategy
organic_selector title_selector link_selector snippet_selector rank_attribute search_property
login password max_pages metrics currency network metrics_source suggestion_type match_mode sandbox depth fresh
sort_by sort_order database
""".split())


class GoogleSearchPlugin:
    descriptor = PluginDescriptor(
        "google-search", "Google Search discovery, organic-competition evidence and expanded Search Console analysis.",
        ("discover", "analyse", "first-party", "provider-api", "import", "organic-competition"),
        (*GSC_REPORT_OPERATIONS, *GSC_INVENTORY_OPERATIONS, *LOCAL_OPERATIONS, *NETWORK_OPERATIONS),
    )

    def __init__(self, *, transport: Any = None) -> None:
        self._transport = transport

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        operation, o = request.operation, request.options
        if operation not in self.descriptor.operations:
            raise ConfigurationError("Unknown google-search operation; see docs/google-search.md.")
        if any(k == "llm" or k.startswith("llm_") for k in o):
            raise ConfigurationError("Google Search source analysis does not use LLM options.")
        unknown = set(o) - OPTIONS
        if any(not k.endswith("_column") for k in unknown):
            raise ConfigurationError("Unknown google-search option; see the documented options for this operation.")
        integer(o, "limit", 100, 1, 50000)
        choice(o, "sort_order", "desc", ("asc", "desc"))
        if "sort_order" in o and "sort_by" not in o:
            raise ConfigurationError("sort_order requires sort_by.")
        if operation in ("serp", "competition", "rank-check", "custom-search", "import-serp", "import-serp-html"):
            integer(o, "top_n", 10, 1, 100)
            if "target_host" in o:
                code(o, "target_host", pattern=r"[A-Za-z0-9.-]+")
                if "." not in o["target_host"]:
                    raise ConfigurationError("target_host must be a hostname.")
        if operation == "gsc-opportunities":
            lo = analysis.option_float(o, "min_position", 4, 0, 1000)
            analysis.option_float(o, "max_position", 20, lo, 1000)
            integer(o, "min_impressions", 100, 0, 10**12)
            analysis.option_float(o, "max_ctr", 1)
            if "target_ctr" in o:
                analysis.option_float(o, "target_ctr", 0)

        now = datetime.now(timezone.utc).isoformat()
        observed = now[:10]
        if operation in LOCAL_OPERATIONS or (operation in GSC_REPORT_OPERATIONS and request.inputs):
            items, metadata, notes = self._local(request, observed)
            return self._finish(request, items, {**metadata, "live_query_performed": False, "request_count": 0}, notes)
        if operation == "page-audit":
            if request.inputs:
                imports.require_files(request)
                captured = text(o, "observed_at")
                from .common import iso_date
                captured = iso_date({"observed_at": captured}, "observed_at")
                items, meta = imports.page_audit(imports.read_path(request.inputs[0], o), request, captured)
                return self._finish(request, items, {"page_audit": meta, "live_query_performed": False, "request_count": 0}, [])
            url = text(o, "url")
            host = public_url(url)
            seeds(request, 20)
            with HTTP(o, (host,), self._transport) as http:
                check_robots(http, url)
                _, body = http.request("GET", url)
                items, meta = imports.page_audit(body, request, observed)
                return self._finish(request, items, {"page_audit": meta, "live_query_performed": True,
                                                   "retrieved_at": now, "request_count": http.requests}, [
                    "Static public HTML only. No recursive crawl, JavaScript execution or indexing verdict."])
        if request.inputs:
            raise ConfigurationError("This network operation does not accept --input files.")
        hosts = console.HOSTS if operation.startswith("gsc-") else providers.PROVIDER_HOSTS
        with HTTP(o, hosts, self._transport) as http:
            if operation in GSC_REPORT_OPERATIONS:
                default = "page" if operation == "gsc-pages" else "query,page" if operation in ("gsc-query-pages", "gsc-overlap") else "query"
                report = console.fetch_report(request, http, observed, default)
                items = self._gsc_items(operation, report, o)
                metadata, notes = {"gsc_report": report}, [analysis.GSC_NOTE]
            elif operation in GSC_INVENTORY_OPERATIONS:
                items, metadata, notes = console.inventory(request, http, observed)
            elif operation in ("serp", "competition", "rank-check"):
                if operation == "rank-check" and "target_host" not in o:
                    raise ConfigurationError("rank-check requires target_host.")
                report = providers.serp(request, http, now)
                items = analysis.serp_competition(report, o)
                if operation == "serp":
                    items.extend(analysis.serp_suggestions(report))
                metadata, notes = {"serp_report": report}, [analysis.SERP_NOTE,
                    "Paid ads, approximate result counts and SERP features are not an organic difficulty score."]
            elif operation == "custom-search":
                report = providers.custom_search(request, http, now)
                items = analysis.serp_competition(report, o)
                metadata, notes = {"serp_report": report}, [report["warning"]]
            elif operation in ("autocomplete", "backlinks", "competitor-keywords", "trends", "ads-url-ideas", "pagespeed"):
                func = {"autocomplete": providers.autocomplete, "backlinks": providers.backlinks,
                        "competitor-keywords": providers.competitor_keywords, "trends": providers.trends,
                        "ads-url-ideas": providers.ads_url_ideas, "pagespeed": providers.pagespeed}[operation]
                items, metadata, notes = func(request, http, observed)
            elif operation == "questions" and o.get("provider", "serpapi") not in ("alsoasked", "ahrefs", "keywordtool"):
                items, metadata, notes = providers.questions(request, http, observed)
            else:
                items, metadata, notes = self._provider(request, http, observed)
            metadata.update(live_query_performed=True, retrieved_at=now, request_count=http.requests)
            return self._finish(request, items, metadata, notes)

    @staticmethod
    def _gsc_items(operation, report, options):
        if operation == "gsc-opportunities":
            return analysis.gsc_opportunities(report, options, report["observed_at"])
        if operation == "gsc-overlap":
            return analysis.query_page_overlap(report, report["observed_at"])
        return analysis.gsc_candidates(report, report["observed_at"])

    @staticmethod
    def _finish(request, items, metadata, notes):
        if "sort_by" in request.options:
            direction = 1 if request.options.get("sort_order") == "asc" else -1
            metric = text(request.options, "sort_by")
            if metric == "phrase":
                items = sorted(items, key=lambda item: item.phrase.casefold(), reverse=direction == -1)
            else:
                valid = {e.metric for item in items for e in item.evidence}
                if items and metric not in valid:
                    raise ConfigurationError("sort_by must name an emitted evidence metric or phrase.")
                def value(item):
                    matches = [e.value for e in item.evidence if e.metric == metric and
                               isinstance(e.value, (int, float)) and not isinstance(e.value, bool)]
                    return matches[0] if len(matches) == 1 else None
                # One measurement, one scope: never choose max of conflicting providers.
                items = sorted(items, key=lambda item: (value(item) is None,
                               direction * (value(item) or 0), item.phrase.casefold()))
        return analysis.finish(request.operation, items, request.options, metadata, notes)

    def _local(self, request, observed):
        operation, o = request.operation, request.options
        if operation == "gsc-bulk-sql":
            if request.inputs or request.keywords:
                raise ConfigurationError("gsc-bulk-sql uses table/property/date options, not keyword or file inputs.")
            sql, meta = console.bulk_sql(o)
            return [], {**meta, "sql": sql}, ["SQL generation only; BigQuery setup/execution and any billed queries remain external."]
        if operation in (*GSC_REPORT_OPERATIONS, "gsc-import"):
            imports.require_files(request)
            path = request.inputs[0]
            report = console.import_report(imports.read_path(path, o), path.suffix.lower(), o)
            return self._gsc_items(operation, report, o), {"gsc_report": report}, [analysis.GSC_NOTE]
        if operation == "gsc-compare":
            imports.require_files(request, 2)
            values = [analysis.unwrap(imports.read_json(p, o), analysis.GSC_SCHEMA, "gsc_report") for p in request.inputs]
            reports = [analysis.gsc_report(v["rows"], v) for v in values]
            return analysis.compare_gsc(*reports, o, observed), {"before_period": [reports[0]["start_date"], reports[0]["end_date"]],
                                                                "after_period": [reports[1]["start_date"], reports[1]["end_date"]]}, [analysis.GSC_NOTE]
        if operation == "import-serp-html":
            report = imports.html_serp(request)
            return analysis.serp_competition(report, o), {"serp_report": report}, [
                "Organic selection and exclusion of ads must be reviewed by the caller; no saved-HTML selector is universally valid."]
        if operation == "import-serp":
            imports.require_files(request)
            report = analysis.validate_serp(analysis.unwrap(imports.read_json(request.inputs[0], o), analysis.SERP_SCHEMA, "serp_report"))
            return analysis.serp_competition(report, o) + analysis.serp_suggestions(report), {"serp_report": report}, [analysis.SERP_NOTE]
        if operation == "serp-compare":
            imports.require_files(request, 2)
            reports = [analysis.unwrap(imports.read_json(p, o), analysis.SERP_SCHEMA, "serp_report") for p in request.inputs]
            items, metadata = analysis.compare_serps(*reports, o)
            return items, metadata, [analysis.SERP_NOTE]
        if operation == "import-observations":
            items, meta = imports.import_observations(request)
            return items, meta, []
        if operation == "observed-compare":
            imports.require_files(request, 2)
            before, after = [obj(imports.read_json(p, o)) for p in request.inputs]
            return imports.compare_observations(before, after, observed), {}, ["Only compatible identities appearing in both snapshots are compared."]
        if operation == "trends-related-import":
            from ..builtin.google_trends import GoogleTrendsPlugin
            selected = {key: o[key] for key in ("observed_at", "scope", "window", "category",
                        "search_property", "seed_keyword", "section", "normalization_id") if key in o}
            if "country" in o:
                selected["geography"] = o["country"]
            result = GoogleTrendsPlugin().run(
                PluginRequest("import-related", inputs=request.inputs, options=selected),
                ExecutionContext(llms=None))
            return list(result.keywords), dict(result.metadata), list(result.notes)
        if operation == "trends-import":
            items, meta = imports.trends_import(request)
            return items, meta, []
        if operation == "keyword-gap":
            imports.require_files(request, 2)
            own, competitor = [obj(imports.read_json(p, o)) for p in request.inputs]
            known = {i.phrase.casefold() for i in analysis.merge_keywords([own])}
            items = [replace(i, relationship="supplied-list-gap") for i in analysis.merge_keywords([competitor]) if i.phrase.casefold() not in known]
            return items, {"own_input": str(request.inputs[0]), "comparison_input": str(request.inputs[1])}, [
                "Absent from the supplied own list does not mean your site never ranks or the keyword has zero traffic."]
        if operation == "combine":
            if not 1 <= len(request.inputs) <= 10:
                raise ConfigurationError("combine requires 1-10 plugin-result JSON files.")
            results = [obj(imports.read_json(p, o)) for p in request.inputs]
            return analysis.merge_keywords(results), {}, ["Evidence is unioned, not averaged; provider scales and geographic scopes remain separate."]
        raise InputError("Unsupported local analysis route.")

    @staticmethod
    def _provider(request, http, observed):
        from .commercial import (
            AhrefsPlugin,
            DataForSEOPlugin,
            KeywordsEverywherePlugin,
            KeywordToolPlugin,
            SemrushPlugin,
        )
        from .discovery import AlsoAskedPlugin
        from .google import GoogleAdsPlugin

        o = dict(request.options)
        operation = request.operation
        default = "google-ads" if operation == "ideas" else "dataforseo" if operation == "metrics" else "keywordtool"
        provider = choice(o, "provider", default, ("google-ads", "dataforseo", "keywordtool", "semrush", "ahrefs", "keywords-everywhere", "alsoasked"))
        routes = {
            ("ideas", "google-ads"): (GoogleAdsPlugin, "ideas"),
            ("metrics", "google-ads"): (GoogleAdsPlugin, "metrics"),
            ("ideas", "dataforseo"): (DataForSEOPlugin, "ideas"),
            ("suggestions", "dataforseo"): (DataForSEOPlugin, "suggestions"),
            ("metrics", "dataforseo"): (DataForSEOPlugin, "metrics"),
            ("suggestions", "keywordtool"): (KeywordToolPlugin, "suggestions"),
            ("questions", "keywordtool"): (KeywordToolPlugin, "suggestions"),
            ("metrics", "keywordtool"): (KeywordToolPlugin, "metrics"),
            ("ideas", "semrush"): (SemrushPlugin, "related"),
            ("suggestions", "semrush"): (SemrushPlugin, "broad-match"),
            ("metrics", "semrush"): (SemrushPlugin, "metrics"),
            ("suggestions", "ahrefs"): (AhrefsPlugin, "matching-terms"),
            ("questions", "ahrefs"): (AhrefsPlugin, "questions"),
            ("metrics", "keywords-everywhere"): (KeywordsEverywherePlugin, "metrics"),
            ("questions", "alsoasked"): (AlsoAskedPlugin, "questions"),
        }
        if (operation, provider) not in routes:
            raise ConfigurationError("This provider does not implement that operation; no fallback was attempted.")
        cls, action = routes[operation, provider]
        if provider == "google-ads":
            o["api_version"] = code(o, "api_version", os.environ.get("GOOGLE_ADS_API_VERSION"), r"v[0-9]+")
        if provider == "keywordtool":
            o["platform"] = "google"
            o.setdefault("network", "googlesearch")
            if operation == "questions":
                o["suggestion_type"] = "questions"
            http.interval = max(http.interval, 4.0)
        # Existing adapters share this run's bounded HTTP object, not new request budgets.
        items, meta, notes = cls().fetch(PluginRequest(action, request.keywords, options=o), http, observed)
        return items, {**meta, "provider": provider, "provider_operation": action}, notes + [
            "Paid-ad competition/CPC and organic difficulty are distinct; estimates retain provider units and geography."]
