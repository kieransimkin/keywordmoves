import hashlib

import pytest

from keywordmoves.builtin.ytkeywordtool import YTKeywordToolPlugin
from keywordmoves.errors import ConfigurationError, InputError
from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.online.commercial import KeywordsEverywherePlugin
from keywordmoves.registry import PluginRegistry

YT_URL = "https://ytkeywordtool.com/"
KE_URL = "https://keywordseverywhere.com/tools/youtube-keyword-tool/"


def yt_html(tier="High", phrase="example song", count=1):
    return f'''<div id="tool-results"><p id="results-count">{count} keywords found</p>
<ul id="keyword-list"><li data-category="Related"><button data-term="{phrase}">
<span>{tier}</span><span>{phrase}</span><span>+</span></button></li></ul></div>'''


def ke_html(volume="1,200", cpc="$0.25", competition="0.15", growth="+36%"):
    return f'''<p>Monthly search volume (Google data) for these YouTube keywords, powered by Keywords Everywhere.</p>
<table class="kt-results-table"><thead><tr><th data-sort="keyword">Keyword</th>
<th data-sort="volume">Volume</th><th data-sort="cpc">CPC</th><th data-sort="competition">Competition</th>
<th data-sort="trend_pct" title="Last month's search volume vs the average of the prior 11 months">Trend %</th>
<th>12-month trend</th></tr></thead><tbody id="kt-results-body"><tr><td>example song</td>
<td>{volume}</td><td>{cpc}</td><td>{competition}</td><td>{growth}</td><td><svg><title>999999</title></svg></td>
</tr></tbody></table>'''


def run(tmp_path, plugin, html, **options):
    path = tmp_path / "public.html"
    path.write_bytes(html.encode("utf-8"))
    settings = {"observed_at": "2026-10-11", "source_url": YT_URL if plugin == "ytkeywordtool" else KE_URL,
                **({"country": "GLB", "currency": "USD"} if plugin != "ytkeywordtool" else {}), **options}
    request = PluginRequest("import-public-html", ("example song",) if plugin == "ytkeywordtool" else (), (path,), settings)
    return PluginRegistry().get(plugin).run(request, ExecutionContext(None))


def test_real_metric_types_stay_separate(tmp_path):
    yt = run(tmp_path, "ytkeywordtool", yt_html())
    candidate = yt.keywords[0]
    assert candidate.evidence[0].value == "High" and candidate.evidence[0].unit == "within_batch_label"
    assert candidate.score is None and candidate.evidence[0].geography is None
    assert candidate.metadata["evidence_kind"] == "language_suggestion"
    assert yt.metadata["input_sha256"] == hashlib.sha256(yt_html().encode()).hexdigest()
    ke = run(tmp_path, "keywords-everywhere", ke_html())
    candidate = ke.keywords[0]
    assert candidate.metadata["platform"] == "Google"
    assert [e.value for e in candidate.evidence] == [1200, 0.25, 0.15, 36]
    assert all(e.metric.startswith("google_") for e in candidate.evidence)
    assert candidate.metadata["google_trend_window"].startswith("Last month's")
    assert candidate.score is None and ke.metadata["youtube_search_volume"] is False
    assert ke.metadata["network_requests"] == yt.metadata["network_requests"] == 0
    assert all(x.metadata["provider_updated_at"] is None for x in (*yt.keywords, *ke.keywords))


def test_unknown_metrics_are_not_zero_and_unmeasured_suggestions_are_excluded(tmp_path):
    result = run(tmp_path, "keywords-everywhere", ke_html("-", "-", "-", "-"))
    assert not result.keywords and result.metadata["unmeasured_rows_skipped"] == 1
    result = run(tmp_path, "keywords-everywhere", ke_html("0", "$0.00", "0.00", "-"))
    assert [e.value for e in result.keywords[0].evidence] == [0, 0, 0, None]


@pytest.mark.parametrize("wrapper", ['<div hidden>{}</div>', '<div aria-hidden="true">{}</div>',
    '<div class="hidden">{}</div>', '<div style="display: none">{}</div>', '<template>{}</template>'])
def test_error_page_does_not_recover_hidden_stale_suggestions(tmp_path, wrapper):
    with pytest.raises(InputError):
        run(tmp_path, "ytkeywordtool", wrapper.format(yt_html()))


def test_scripts_and_hidden_duplicate_rows_do_not_become_data(tmp_path):
    html = '<script>' + yt_html() + '</script><div hidden>' + yt_html() + '</div>' + yt_html()
    assert len(run(tmp_path, "ytkeywordtool", html).keywords) == 1


@pytest.mark.parametrize("html", [yt_html("Huge"), yt_html(count=0), yt_html() * 2,
    yt_html().replace('data-term="example song"', 'data-term="different phrase"'),
    yt_html().replace("</ul>", yt_html().split('<ul id="keyword-list">')[1])])
def test_changed_or_duplicate_suggestion_schema_fails(tmp_path, html):
    with pytest.raises(InputError):
        run(tmp_path, "ytkeywordtool", html)


def test_filtered_capture_is_explicitly_partial(tmp_path):
    assert run(tmp_path, "ytkeywordtool", yt_html(count=3)).metadata["completeness"] == "partial"


@pytest.mark.parametrize("options", [{"source_url": "https://example.com/"}, {"observed_at": "2099-01-01"},
    {"observed_at": "2026-10-11T10:00:00"}, {"api_key": "not-used"}])
def test_capture_source_time_and_option_guards(tmp_path, options):
    with pytest.raises((ConfigurationError, InputError)):
        run(tmp_path, "ytkeywordtool", yt_html(), **options)


@pytest.mark.parametrize("options", [{"country": None}, {"country": "worldwide"}, {"currency": "UNKNOWN"}, {"currency": "GBP"}, {"currency": []}])
def test_google_scope_and_currency_must_be_reviewed(tmp_path, options):
    with pytest.raises((ConfigurationError, InputError)):
        run(tmp_path, "keywords-everywhere", ke_html(), **options)


@pytest.mark.parametrize("html", [ke_html(volume="NaN"), ke_html(volume="1.2K"), ke_html(volume="1.5"),
    ke_html(competition="1.5"), ke_html(cpc="-2"), ke_html(growth="12"),
    ke_html().replace("Google data", "YouTube data"),
    ke_html().replace("Last month's search volume vs the average of the prior 11 months", "Last week"),
    ke_html().replace("</tbody>", ke_html().split('<tbody id="kt-results-body">')[1])])
def test_google_metric_and_scope_changes_fail(tmp_path, html):
    with pytest.raises(InputError):
        run(tmp_path, "keywords-everywhere", html)


def test_plain_offline_import_does_not_construct_a_network_client(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Offline import attempted the live provider path")
    monkeypatch.setattr(KeywordsEverywherePlugin, "fetch", fail)
    assert len(run(tmp_path, "keywords-everywhere", ke_html()).keywords) == 1


def test_batch_composes_both_offline_modules_but_rejects_live_api(tmp_path):
    import json
    yt_path, ke_path, plan_path = (tmp_path / name for name in ("yt.html", "ke.html", "plan.json"))
    yt_path.write_text(yt_html(), "utf-8")
    ke_path.write_text(ke_html(), "utf-8")
    tasks = [
        {"id": "suggestions", "subject": "example", "plugin": "ytkeywordtool", "operation": "import-public-html",
         "keywords": ["example song"], "inputs": [str(yt_path)], "options": {"source_url": YT_URL, "observed_at": "2026-10-11"}},
        {"id": "google-context", "subject": "example", "plugin": "keywords-everywhere", "operation": "import-public-html",
         "inputs": [str(ke_path)], "options": {"source_url": KE_URL, "observed_at": "2026-10-11", "country": "GLB", "currency": "USD"}},
    ]
    plan_path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": tasks}), "utf-8")
    request = PluginRequest("collect", inputs=(plan_path,))
    result = PluginRegistry().get("research-batch").run(request, ExecutionContext(None))
    assert len(result.keywords) == 2 and result.metadata["unavailable_tasks"] == 0
    assert {k.metadata["platform"] for k in result.keywords} == {"Google", "YouTube"}
    tasks[1]["operation"] = "metrics"
    plan_path.write_text(json.dumps({"schema": "keywordmoves-research-batch/v1", "tasks": tasks}), "utf-8")
    with pytest.raises(InputError, match="metered"):
        PluginRegistry().get("research-batch").run(request, ExecutionContext(None))


def test_import_inputs_and_seed_are_explicit(tmp_path):
    with pytest.raises(ConfigurationError):
        YTKeywordToolPlugin().run(PluginRequest("import-public-html"), ExecutionContext(None))
