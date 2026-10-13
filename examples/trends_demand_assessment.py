"""Synthetic matched comparison; no network or absolute-volume claims."""
from pathlib import Path
from tempfile import TemporaryDirectory

from keywordmoves.builtin.google_trends import GoogleTrendsPlugin
from keywordmoves.models import ExecutionContext, PluginRequest

with TemporaryDirectory() as folder:
    path = Path(folder) / "comparison.csv"
    path.write_text("Week,example song: (Worldwide),second song: (Worldwide)\n2026-10-04,25,<1\n2026-10-11,100,100\n", encoding="utf-8")
    result = GoogleTrendsPlugin().run(PluginRequest("assess-interest", inputs=(path,), options={
        "search_property": "youtube", "geography": "GLB", "observed_at": "2026-10-13"}), ExecutionContext(None))
    assert result.keywords[0].metadata["demand_profile"]["mean_index"] == 25
    assert result.keywords[1].metadata["demand_profile"]["mean_index"] is None
    assert result.keywords[1].metadata["demand_profile"]["mean_index_bounds"]["upper"] == 1
    assert all(k.score is None for k in result.keywords)
    print("Synthetic comparison assessed; censoring bounds retained, no invented volume.")
