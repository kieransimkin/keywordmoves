# Importing vidIQ and reviewing phrase fit

`vidiq import-json` is an offline importer for saved, authorised vidIQ MCP
keyword results and explicitly reviewed browser captures. It has no web scraper,
undocumented API, account access, paid calls or automatic research expansion.
Use the connected MCP tool or permitted native UI/export to collect data first.

~~~powershell
keywordmoves run vidiq --operation import-json --input saved-result.json --option observed_at=2026-10-11 --option "subject=Example Song"
keywordmoves run candidate-focus --operation related --keyword "grief song" --input keywords.json --option "include_any=song|songs|music" --option "exclude_any=preschool|kids|type beat" --option limit=20
keywordmoves run candidate-focus --operation matching-terms --keyword "grief song" --input keywords.json
~~~

The import accepts the raw keyword JSON, MCP `structuredContent`, or the MCP
text block with `Keyword data (JSON):` followed by its JSON. Supply capture time
via `observed_at`. A native reviewed envelope may instead include:

~~~json
{"schema":"keywordmoves-vidiq-capture/v1","observed_at":"2026-10-11","capture_route":"native-browser","completeness":"partial","request":{"mode":"research","keyword":"example song"},"result":{"mode":"research","country":null,"seedKeyword":{"keyword":"example song","estimatedMonthlySearch":null,"monthlySearchesDisplay":"<750","metricsAsOf":null},"relatedKeywords":[]}}
~~~

The envelope records review, not the authenticity of an arbitrary file. Preserve
the original permitted capture, and optionally its `capture_reference` and
`capture_sha256`. Do not copy locked placeholder tables into reviewed results.
Native CSV exports can already use [native-export](native-export.md) with the
actual columns and semantics; this importer does not guess CSV headers.

Worldwide monthly estimates and the requested `countryVolume` stay separate.
A GB request does not make the global scores GB-only. A country estimate without
a known country is rejected. The `<750` display takes precedence over a raw
numeric zero placeholder; an uncensored, actually reported zero stays zero.
Missing values stay null. Provider volume, competition, overall and related
indices, baseline volume and measured growth retain their own units. Candidate
scores remain unset: an overall vidIQ index is provider evidence, not a universal
KeywordMoves score. Related/question/matching/rising rows preserve upstream order.

`metrics_as_of` is distinct from the capture date. `freshness` uses that provider
date and a caller-controlled `max_age_days` threshold (default 30, range 1–366);
unknown dates remain unknown, including native displays without a metrics date.
`within_threshold` is a date check, not a certificate of accuracy or current
demand. Preserve original country, top-market context, provider notes and hashes.

`candidate-focus` accepts 1–20 saved KeywordMoves results and one exact seed.
`related` keeps at least `min_overlap` meaningful seed terms (default 1, explicit
0 allowed). `matching-terms` requires all meaningful terms. A small documented
English stopword list removes a/an/the/about/for/of/to/in/and/with. Terms otherwise
use Unicode NFC and casefolding, without stemming, translations or substring
matches. Exact seed matches rank first, then greater term overlap, then input
order. `include_any` and `exclude_any` use pipe-separated literal phrase lists,
with whole-token contiguous matching. `subject` optionally restricts the queue
to an exact source subject. No subject association is inferred.

Every retained candidate keeps its original score/evidence/metadata and receives
an explainable review record. Inputs and settings are hashed; rejected rows have
explicit reasons. `limit` (1–1000) records how many qualifying rows were omitted.
An empty queue is valid. No minimum keyword count is filled with off-topic words.
Literal fit needs human intent review: related music may still target a different
artist, nursery content, tutorials or instrumentals. Do not turn these filters
into a claim of independently measured relevance or predicted marketing uplift.

Both plugins compose with [research-batch](multi-source-research.md). See
`examples/vidiq_demo.py` for a synthetic, offline, reproducible demonstration.

## A fair comparison

Freeze identical seed strings, platform, route, country, language where supported,
capture dates, suggestion mode, visible row bounds and review criteria. Distinguish
native browser limits from paid MCP limits. Record unavailable routes, empty
results and costs; saved replay incurs no new provider charge. Compare discovery
coverage and manually reviewed intent with a stated rubric. Compare numeric
metrics only where provider, scope, unit and period actually match. A tool using
vidIQ's imported estimates has integrated that evidence; it has not independently
reproduced or beaten vidIQ's estimation model. A small exploratory benchmark and
source count do not prove general superiority.

Official context: [vidIQ keyword research](https://support.vidiq.com/en/articles/9421214-keywords-research),
[vidIQ MCP](https://support.vidiq.com/en/articles/15082430-vidiq-mcp).
