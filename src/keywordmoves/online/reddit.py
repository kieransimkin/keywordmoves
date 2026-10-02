"""Read-only Reddit discovery and evidence, with explicit access and sample limits."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from ..errors import ConfigurationError, InputError
from ..models import (
    ExecutionContext,
    KeywordCandidate,
    PluginDescriptor,
    PluginRequest,
    PluginResult,
)
from . import reddit_analysis as analysis
from . import reddit_imports as imports
from .common import (
    HTTP,
    OnlineSourceError,
    array,
    boolean,
    choice,
    integer,
    obj,
    secret,
    seeds,
    text,
)

NATIVE = ("search", "hashtag", "posts", "subreddit-feed", "comments", "more-comments",
          "communities", "community-autocomplete", "community", "community-rules", "duplicates", "link-posts", "refresh")
LOCAL = ("import-posts", "import-comments", "import-communities", "import-observations",
         "import-html", "analyse-text", "compare", "redact")
PROVIDERS = ("suggestions", "metrics", "web-search", "apify-start", "apify-fetch")
TIME = ("hour", "day", "week", "month", "year", "all")
SORT = ("relevance", "hot", "top", "new", "comments")
OPTIONS = analysis.ANALYSIS_OPTIONS | set("""
access_token user_agent api_access_approved max_requests max_response_bytes timeout min_interval
subreddit pages page_size after sort time post_id comment_ids comment_sort depth literal
scope source observed_at text kind row_selector empty_selector delimiter
api_key country language currency metrics suggestion_type sandbox
actor run_id dataset_kind allow_paid collection_authorized max_charge_usd results_per_seed
include_comments comment_limit dataset_offset
""".split())


class RedditHTTP(HTTP):
    """Keep shared transport behaviour; additionally stop when Reddit's quota is exhausted."""
    def __init__(self, options, transport=None):
        super().__init__(options, ("oauth.reddit.com",), transport, interval=1.0)
        if not boolean(options, "api_access_approved"):
            raise ConfigurationError("Reddit requires approved access: declare api_access_approved=true only after approval.")
        self.token = secret(options, "access_token", "REDDIT_ACCESS_TOKEN")
        self.user_agent = text(options, "user_agent", os.environ.get("REDDIT_USER_AGENT"))
        if not re.fullmatch(r"[^\s:]+:[^\s:]+:[^\s]+ \(by /u/[A-Za-z0-9_-]+\)", self.user_agent):
            raise ConfigurationError("REDDIT_USER_AGENT must identify platform:app:version (by /u/your_username).")
        self.rate_limit: dict[str, float] = {}

    def __enter__(self):
        super().__enter__()
        self.client.headers.update({"Authorization": "Bearer " + self.token, "User-Agent": self.user_agent})
        self.client.event_hooks["response"].append(self._capture)
        return self

    def _capture(self, response):
        # Not cached across runs: applications sharing an OAuth client must coordinate their quota.
        for key in ("remaining", "reset", "used"):
            raw = response.headers.get("x-ratelimit-" + key)
            if raw is not None:
                try:
                    val = float(raw)
                    if math.isfinite(val) and val >= 0:
                        self.rate_limit[key] = val
                except ValueError:
                    pass

    def get(self, path: str, params: dict | None = None):
        if self.rate_limit.get("remaining", 1) < 1:
            raise OnlineSourceError("Reddit's rate-limit budget is exhausted. Stop until its reset; no retry was made.")
        if not path.startswith("/") or ".." in path or "?" in path:
            raise ConfigurationError("Invalid Reddit endpoint path.")
        result = self.json("GET", "https://oauth.reddit.com" + path, params={"raw_json": 1, **(params or {})})
        if isinstance(result, dict) and (result.get("error") or result.get("errors")):
            raise OnlineSourceError("Reddit returned an API error; check approval, scope, quota and permissions.")
        return result


def _scope(operation: str, request: PluginRequest) -> str:
    keep = ("subreddit", "sort", "time", "page_size", "pages", "after", "post_id", "comment_sort",
            "literal", "include_nsfw", "exclude_stickied", "comment_ids", "depth")
    # Canonicalise meaningful defaults so snapshots are comparable only within the same collector.
    defaults = {"pages": 1, "page_size": 50, "time": "month", "sort": "relevance",
                "comment_sort": "confidence", "literal": False, "include_nsfw": False,
                "exclude_stickied": False}
    if operation in {"subreddit-feed", "duplicates"}:
        defaults["sort"] = "new"
    cfg = {k: request.options.get(k, defaults.get(k)) for k in keep}
    return operation + ":" + json.dumps({"queries": list(request.keywords), **cfg}, sort_keys=True)


def _one_file(request: PluginRequest):
    if len(request.inputs) != 1:
        raise ConfigurationError("This operation requires exactly one --input file.")
    return request.inputs[0]


class RedditPlugin:
    descriptor = PluginDescriptor(
        "reddit", "Reddit community, conversation, keyword and attention research with explicit provenance.",
        ("discover", "analyse", "provider-api", "first-party", "import", "read-only"),
        (*NATIVE, *LOCAL, *PROVIDERS, "competition"),
    )

    def __init__(self, *, transport: Any = None):
        self._transport = transport

    def run(self, request: PluginRequest, context: ExecutionContext) -> PluginResult:
        del context
        o, operation = request.options, request.operation
        if operation not in self.descriptor.operations:
            raise ConfigurationError("Unknown reddit operation; see docs/reddit.md.")
        if any(k == "llm" or k.startswith("llm_") for k in o):
            raise ConfigurationError("Reddit analysis does not send source content to an LLM.")
        if any(k not in OPTIONS and not k.endswith(("_column", "_selector")) for k in o):
            raise ConfigurationError("Unknown reddit option; see docs/reddit.md.")
        integer(o, "limit", 50, 1, 1000)
        # Validate extraction settings even with empty responses, before any paid/network call.
        analysis.validate(o)
        for flag in ("include_records", "exclude_stickied", "include_nsfw"):
            boolean(o, flag)
        if operation in LOCAL or (operation == "competition" and request.inputs):
            result = self._local(request)
            return replace(result, metadata={**result.metadata, "live_query_performed": False, "request_count": 0})
        if request.inputs:
            raise ConfigurationError("Network operations do not accept --input files.")
        if operation in PROVIDERS:
            from .reddit_providers import run_provider
            return run_provider(request, self._transport)
        if any(k in o for k in ("country", "language", "source", "scope", "observed_at")):
            raise ConfigurationError("Native Reddit collection sets its own source, scope and capture time; country/language targeting is unsupported.")
        with RedditHTTP(o, self._transport) as http:
            now = datetime.now(timezone.utc).isoformat()
            ctx = analysis.context("Reddit Data API", _scope(operation, request), now,
                                   live_query_performed=True, access="approved-oauth",
                                   current_access_notice="Approved legacy OAuth route. Reddit has announced closure of remaining public API access in March 2027; see docs/reddit.md.")
            result = self._native(request, http, ctx)
            return replace(result, metadata={**result.metadata, "request_count": http.requests,
                                            "rate_limit": dict(http.rate_limit)})

    def _listing(self, http, path, params, options):
        rows, after, visited = [], options.get("after"), set()
        if after is not None:
            after = analysis.fullname(after)
            visited.add(after)
        page_size = integer(options, "page_size", 50, 1, 100)
        pages = integer(options, "pages", 1, 1, 10)
        for _ in range(pages):
            payload = obj(http.get(path, {**params, "limit": page_size, **({"after": after} if after else {})}))
            if payload.get("kind") != "Listing":
                raise OnlineSourceError("Expected a Reddit Listing; no empty-result assumption was made.")
            data = obj(payload.get("data"))
            rows.extend(array(data.get("children")))
            after = data.get("after")
            if after is not None:
                after = analysis.fullname(after)
            if not after:
                break
            if after in visited:
                raise OnlineSourceError("Reddit repeated a pagination cursor; no further request was made.")
            visited.add(after)
        maximum = integer(options, "max_records", 1000, 1, 10000)
        if len(rows) > maximum:
            raise InputError("Collected rows exceeded max_records; reduce pagination or increase that bound.")
        positions = [{"id": row.get("data", {}).get("name"), "position_in_returned_listing": i}
                     for i, row in enumerate(rows, 1) if isinstance(row, dict)]
        return rows, {"next_after": after, "listing_exhausted": after is None,
                      "listing_positions": positions, "position_scope": "this returned listing only",
                      "collection_complete": False, "completeness_note": "A bounded listing is not a population census."}

    def _native(self, request, http, ctx):
        o, operation = request.options, request.operation
        prefix = "/r/" + analysis.subreddit(o["subreddit"]) if "subreddit" in o else ""
        if operation in {"search", "hashtag", "competition"}:
            query = seeds(request)[0]
            if operation == "hashtag":
                tag = query.lstrip("#")
                if not re.fullmatch(r"[^\W_][\w]*", tag, re.UNICODE):
                    raise ConfigurationError("Supply a single literal hashtag without spaces.")
                query = "#" + tag
            sent_query = '"' + query.replace('"', '') + '"' if (
                operation == "hashtag" or boolean(o, "literal")) else query
            rows, meta = self._listing(http, prefix + "/search", {
                "q": sent_query, "restrict_sr": bool(prefix), "type": "link",
                "sort": choice(o, "sort", "relevance", SORT),
                "t": choice(o, "time", "month", TIME),
            }, o)
            ctx = {**ctx, **meta, "search_query": query}
            if operation == "competition":
                return analysis.competition(rows, query, o, ctx)
            if operation == "hashtag":
                selected = []
                for row in rows:
                    data = obj(row.get("data"))
                    found = analysis.HASH.finditer(analysis.clean(data.get("title")) + "\n" + analysis.clean(data.get("selftext")))
                    if query.casefold() in {"#" + m[1].casefold() for m in found}:
                        selected.append(row)
                ctx["search_hits_without_literal_hashtag"] = len(rows) - len(selected)
                rows = selected
            return analysis.analyse(rows, o, ctx, operation)
        if operation == "subreddit-feed":
            if not prefix:
                raise ConfigurationError("subreddit-feed requires a subreddit option.")
            order = choice(o, "sort", "new", ("new", "hot", "top", "rising", "controversial"))
            params = {"t": choice(o, "time", "month", TIME)} if order in {"top", "controversial"} else {}
            rows, meta = self._listing(http, prefix + "/" + order, params, o)
            return analysis.analyse(rows, o, {**ctx, **meta}, operation)
        if operation in {"posts", "refresh", "link-posts"}:
            if operation == "link-posts":
                url = seeds(request)[0]
                if analysis.safe_url(url) != url:
                    raise ConfigurationError("Supply a plain public HTTP(S) URL without query credentials/fragments.")
                params = {"url": url}
                requested = []
            else:
                requested = [analysis.fullname(v, "t3" if operation == "posts" else None)
                             for v in seeds(request, 100)]
                if any(v.startswith("t5_") for v in requested):
                    raise ConfigurationError("Use community for subreddit IDs; refresh accepts posts/comments.")
                params = {"id": ",".join(requested)}
            rows, _ = imports.flatten(http.get(prefix + "/api/info", params))
            found = [analysis.fullname(r["data"].get("name") or r["data"].get("id"), r["kind"]) for r in rows]
            # Refresh outputs no stale text: missing objects are excluded, not classified as deleted.
            return analysis.analyse(rows, o, {**ctx, "requested_ids": requested,
                                    "not_returned_ids": sorted(set(requested) - set(found)),
                                    "refresh_note": "Replace cached source records; also remove old derived outputs."}, operation)
        if operation in {"comments", "more-comments"}:
            identity = analysis.fullname(text(o, "post_id"), "t3")
            sort = choice(o, "comment_sort", "confidence", ("confidence", "top", "new", "controversial", "old", "qa"))
            if operation == "comments":
                payload = http.get("/comments/" + identity[3:], {"sort": sort,
                    "limit": integer(o, "page_size", 100, 1, 500),
                    "depth": integer(o, "depth", 8, 1, 20)})
            else:
                values = text(o, "comment_ids").split(",")
                if not 1 <= len(values) <= 100 or any(not re.fullmatch(r"[a-z0-9]{1,20}", v) for v in values):
                    raise ConfigurationError("comment_ids must be 1-100 comma-separated base36 comment IDs.")
                payload = obj(http.get("/api/morechildren", {"api_type": "json", "link_id": identity,
                                                           "children": ",".join(values), "sort": sort}))
                packed = obj(payload.get("json"))
                if packed.get("errors"):
                    raise OnlineSourceError("Reddit could not expand the requested comments.")
                payload = obj(packed.get("data")).get("things")
            rows, more = imports.flatten(payload, integer(o, "max_records", 1000, 1, 10000))
            rows = [r for r in rows if r["kind"] == "t1"]
            return analysis.analyse(rows, o, {**ctx, "post_id": identity, "more_comment_ids": more,
                                    "comment_tree_complete": False}, operation)
        if operation == "duplicates":
            if "after" in o or integer(o, "pages", 1, 1, 10) != 1:
                raise ConfigurationError("duplicates currently collects one bounded response, not multiple pages.")
            identity = analysis.fullname(text(o, "post_id"), "t3")
            payload = http.get("/duplicates/" + identity[3:], {
                "sort": choice(o, "sort", "new", ("new", "num_comments")),
                "limit": integer(o, "page_size", 50, 1, 100)})
            rows, _ = imports.flatten(payload)
            rows = [r for r in rows if r["data"].get("name") != identity]
            return analysis.analyse(rows, o, {**ctx, "parent_post_id": identity, "collection_complete": False}, operation)
        if operation == "community-rules":
            if not prefix:
                raise ConfigurationError("community-rules requires a subreddit option.")
            payload = obj(http.get(prefix + "/about/rules"))
            rules = array(payload.get("rules"))
            output = []
            for i, raw in enumerate(rules, 1):
                row = obj(raw)
                output.append(KeywordCandidate(text(row, "short_name"), "reddit-community-rule", None,
                    (analysis.evidence(ctx, "rule_order", i, "ordinal"),),
                    {"description": analysis.clean(row.get("description")), "kind": row.get("kind")}))
            limit = integer(o, "limit", 50, 1, 1000)
            return PluginResult("reddit", operation, tuple(output[:limit]), (
                "Rules are context for participation, not keyword-demand signals; this tool does not post.",),
                {**ctx, "output_truncated": len(output) > limit, "rows_received": len(output)})
        if operation == "community":
            if not prefix:
                raise ConfigurationError("community requires a subreddit option.")
            rows = [http.get(prefix + "/about")]
            return analysis.communities(rows, ctx, o, operation)
        if operation in {"communities", "community-autocomplete"}:
            query = seeds(request)[0]
            if operation == "community-autocomplete":
                if len(query) > 25:
                    raise ConfigurationError("Community autocomplete query cannot exceed 25 characters.")
                payload = http.get("/api/subreddit_autocomplete_v2", {"query": query, "include_profiles": False,
                    "include_over_18": boolean(o, "include_nsfw"), "limit": integer(o, "page_size", 5, 1, 10)})
                rows, _ = imports.flatten(payload)
                meta = {"autocomplete_kind": "community-names-not-search-query-volume"}
            else:
                rows, meta = self._listing(http, "/subreddits/search", {"q": query,
                                         "include_over_18": boolean(o, "include_nsfw")}, o)
            return analysis.communities(rows, {**ctx, **meta}, o, operation)
        raise ConfigurationError("Unsupported native route.")

    def _local(self, request):
        o, operation = request.options, request.operation
        if operation == "compare":
            return imports.compare(request.inputs, o)
        if operation == "import-observations":
            return imports.observations(_one_file(request), o)
        if operation == "redact":
            payload = imports.load_json(_one_file(request))
            if not isinstance(payload, dict) or payload.get("plugin") != "reddit":
                raise InputError("redact needs a saved Reddit analysis with include_records=true.")
            meta = obj(payload.get("metadata"))
            rows = meta.get("records")
            if not isinstance(rows, list):
                raise InputError("Snapshot has no records; delete it and regenerate from current authorised data.")
            ids = set(analysis.fullname(v) for v in seeds(request, 100))
            if any(not isinstance(r, dict) for r in rows):
                raise InputError("Saved records must be objects.")
            kept = [r for r in rows if r.get("id") not in ids]
            previous = meta.get("analysis_options", {})
            if not isinstance(previous, dict) or not set(previous) <= analysis.ANALYSIS_OPTIONS:
                raise InputError("Saved analysis options have an invalid shape.")
            o = {**previous, **o}
            analysis.validate(o)
            ctx = analysis.context(text(meta, "source"), text(meta, "scope"), meta.get("observed_at"),
                                   redacted_ids=sorted(ids), original_file_modified=False)
            return analysis.analyse(kept, o, ctx, operation)
        ctx = analysis.context(text(o, "source", "Reference text" if operation == "analyse-text" else None),
                               text(o, "scope", "reference-text" if operation == "analyse-text" else None),
                               o.get("observed_at") or (datetime.now(timezone.utc).isoformat()
                                                        if operation == "analyse-text" else None))
        if operation == "analyse-text":
            if request.inputs:
                value = imports.read(_one_file(request))
            else:
                value = o.get("text")
            if not isinstance(value, str) or not value.strip():
                raise InputError("Supply reference text with --input or --option text=...")
            rows = [{"kind": "t3", "id": "t3_localreference", "title": value}]
            result = analysis.analyse(rows, o, {**ctx, "known_status": "reference-only-not-verified-on-reddit"}, operation)
            return result
        if operation == "import-communities":
            rows = imports.imported_records(_one_file(request), o, "t5")
            return analysis.communities([r for r in rows if r.get("kind") == "t5"], ctx, o, operation)
        kind = choice(o, "kind", "t1" if operation == "import-comments" else "t3", ("t1", "t3"))
        rows = imports.html_records(_one_file(request), o, kind) if operation == "import-html" else (
            imports.imported_records(_one_file(request), o, kind))
        if operation == "competition":
            return analysis.competition(rows, seeds(request)[0], o, ctx)
        return analysis.analyse(rows, o, ctx, operation)
