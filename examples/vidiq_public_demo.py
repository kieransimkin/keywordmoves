"""Synthetic public-feed import; no HTTP request or account needed."""
import argparse
import json
from pathlib import Path

from keywordmoves.models import ExecutionContext, PluginRequest
from keywordmoves.registry import PluginRegistry

parser = argparse.ArgumentParser()
parser.add_argument("--output-dir", type=Path, default=Path("public-demo"))
args = parser.parse_args()
args.output_dir.mkdir(parents=True, exist_ok=True)
path = args.output_dir / "synthetic-public-widget.html"
path.write_text('''<section><h2>The most searched keywords on YouTube right now</h2>
<p>Updated October 11, 2026.</p>
<div><h3>Top Searched Keywords</h3><p>Highest search volume in the United States</p><ol>
<li><span>1</span><span>example phrase</span><p>1.2M</p><p>searches / mo</p></li></ol></div>
<div><h3>Rising Keywords</h3><p>Fastest growing search terms, past 7 days</p><ol>
<li><span>1</span><span>another phrase</span><p>+2,500%</p><p>vs. previous period</p></li></ol></div></section>''', encoding="utf-8")
result = PluginRegistry().get("vidiq").run(PluginRequest("import-public-html", inputs=(path,),
    options={"source_url": "https://vidiq.com/features/keyword-tools/", "observed_at": "2026-10-11"}), ExecutionContext(None))
assert result.keywords[0].evidence[0].value == "1.2M"
assert result.keywords[1].evidence[0].geography is None
assert result.metadata["network_requests"] == 0
(args.output_dir / "result.json").write_text(json.dumps(result.to_dict(), indent=2), "utf-8")
print("Synthetic public-feed import passed: rounded US monthly counts and geography-unspecified growth remain separate.")
