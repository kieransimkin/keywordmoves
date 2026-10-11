"""Synthetic offline suggestion and Google-context composition."""
import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    out = parser.parse_args().output_dir
    out.mkdir(parents=True, exist_ok=True)
    yt = '''<div id="tool-results"><p id="results-count">1 keywords found</p>
<ul id="keyword-list"><li data-category="Related"><button data-term="synthetic friendship song">
<span>High</span><span>synthetic friendship song</span><span>+</span></button></li></ul></div>'''
    ke = '''<p>Monthly search volume (Google data) for these YouTube keywords</p>
<table class="kt-results-table"><thead><tr><th data-sort="keyword">Keyword</th>
<th data-sort="volume">Volume</th><th data-sort="cpc">CPC</th><th data-sort="competition">Competition</th>
<th data-sort="trend_pct" title="Last month's search volume vs the average of the prior 11 months">Trend %</th>
<th>12-month trend</th></tr></thead><tbody id="kt-results-body"><tr><td>synthetic friendship song</td>
<td>1,200</td><td>$0.25</td><td>0.15</td><td>+36%</td><td>-</td></tr></tbody></table>'''
    for name, data in (("synthetic-yt.html", yt), ("synthetic-ke.html", ke)):
        (out / name).write_bytes(data.encode("utf-8"))
    plan = {"schema": "keywordmoves-research-batch/v1", "tasks": [
        {"id": "suggestions", "subject": "Synthetic example", "plugin": "ytkeywordtool", "operation": "import-public-html",
         "keywords": ["synthetic friendship"], "inputs": [str(out / "synthetic-yt.html")],
         "options": {"source_url": "https://ytkeywordtool.com/", "observed_at": "2026-10-11"}},
        {"id": "google-context", "subject": "Synthetic example", "plugin": "keywords-everywhere", "operation": "import-public-html",
         "inputs": [str(out / "synthetic-ke.html")], "options": {
             "source_url": "https://keywordseverywhere.com/tools/youtube-keyword-tool/", "observed_at": "2026-10-11",
             "country": "GLB", "currency": "USD"}},
    ]}
    path = out / "synthetic-plan.json"
    path.write_text(json.dumps(plan, indent=2), "utf-8")
    result = PluginRegistry().get("research-batch").run(PluginRequest("collect", inputs=(path,)), ExecutionContext(None))
    assert len(result.keywords) == 2 and result.metadata["unavailable_tasks"] == 0
    assert {item.metadata["platform"] for item in result.keywords} == {"Google", "YouTube"}
    (out / "synthetic-result.json").write_text(json.dumps(result.to_dict(), indent=2), "utf-8")
    print("Synthetic example passed: 2 separately labelled rows, no network calls.")


if __name__ == "__main__":
    main()
