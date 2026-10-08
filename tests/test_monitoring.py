"""Monitoring tests exercise misleading-data and operational-failure cases."""
from __future__ import annotations

import hashlib
import json

import pytest

from keywordmoves.errors import InputError
from keywordmoves.models import KeywordCandidate, KeywordEvidence, PluginResult
from keywordmoves.monitoring import analysis
from keywordmoves.monitoring.imports import decode_payload
from keywordmoves.monitoring.runner import collect, resolved_collector
from keywordmoves.monitoring.store import MonitorStore
from keywordmoves.monitoring.validation import SCHEMA, observation, watch

NOW = "2026-10-08T18:00:00+00:00"


def row(**changes):
    return {
        "subject": "Example Song", "platform": "Google", "phrase": "example song",
        "source": "Google Search Console", "metric": "impressions", "unit": "count",
        "scope": "https://example.com/song/", "geography": "GB", "window": "7 days",
        "evidence_kind": "property_performance", "observed_at": "2026-10-07",
        "value": 10, "completeness": "complete", "approximate": False,
        "period_start": "2026-09-28", "period_end": "2026-10-04",
        **changes,
    }


def watched(**changes):
    return watch({
        "id": "example-google", "subject": "Example Song", "platform": "Google",
        "phrase": "example song", "source": "Google Search Console", "metric": "impressions",
        "unit": "count", "scope": "https://example.com/song/", "geography": "GB",
        "window": "7 days", "evidence_kind": "property_performance",
        "min_absolute_change": 1, **changes,
    })


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def store(tmp_path):
    with MonitorStore(tmp_path / "private.sqlite", create=True) as instance:
        file = write_json(tmp_path / "watches.json", {
            "schema": "keywordmoves-watchlist/v1", "watches": [watched()]})
        instance.add_watchlist(file, now=NOW)
        yield instance


def ingest(store, tmp_path, rows, name="evidence.json"):
    path = write_json(tmp_path / name, {"schema": SCHEMA, "observations": rows})
    return store.ingest(path, now=NOW)


def two_periods():
    return [row(value=10, observed_at="2026-09-30", period_start="2026-09-21",
                period_end="2026-09-27"), row(value=20)]


def test_import_is_atomic_idempotent_and_keeps_exact_raw_bytes(store, tmp_path):
    path = write_json(tmp_path / "raw.json", {"schema": SCHEMA, "observations": [row()]})
    original = path.read_bytes()
    first = store.ingest(path, now=NOW)
    second = store.ingest(path, now=NOW)
    assert first["inserted"] == 1 and second["inserted"] == 0
    assert second["duplicate_snapshot"] is True
    assert path.read_bytes() == original
    saved = store.db.execute("SELECT raw,sha256 FROM snapshots").fetchone()
    assert saved["raw"] == original
    assert saved["sha256"] == hashlib.sha256(original).hexdigest()
    bad = write_json(tmp_path / "bad.json", {"schema": SCHEMA, "observations": [
        row(), row(value=float("nan"))]})
    with pytest.raises(InputError):
        store.ingest(bad, now=NOW)
    assert len(store.observations(as_of=NOW)) == 1
    assert store.verify()["verified"]


def test_validation_failure_leaves_no_partial_observations(store, tmp_path):
    with pytest.raises(InputError):
        ingest(store, tmp_path, [row(), row(platform="")])
    assert store.observations(as_of=NOW) == []


@pytest.mark.parametrize("changes", [
    {"value": True}, {"value": float("inf")}, {"observed_at": "2026-10-09"},
    {"observed_at": "2026-10-07T12:00:00"}, {"period_end": "2026-10-09"},
    {"period_start": None}, {"availability": "unavailable", "value": 0},
    {"dimensions": {"access_token": "synthetic-secret"}},
    {"dimensions": {"x": float("nan")}}, {"evidence_kind": "viral-score"},
])
def test_misleading_or_invalid_measurements_are_rejected(changes):
    with pytest.raises(InputError):
        observation(row(**changes), now=NOW)


def test_unavailable_does_not_become_zero_and_last_good_is_preserved(store, tmp_path):
    ingest(store, tmp_path, [row(observed_at="2026-10-06"),
                            row(value=None, availability="unavailable", observed_at="2026-10-07")])
    output = analysis.coverage(store, now=NOW)["rows"][0]
    assert output["status"] == "unavailable"
    assert output["latest"]["value"] is None
    assert output["last_observed"]["value"] == 10
    assert output["has_demand_signal"] is False


def test_language_suggestions_are_not_counted_as_demand(store, tmp_path):
    ingest(store, tmp_path, [row(metric="native-search-language", value="related wording",
                               evidence_kind="language_suggestion", period_start=None, period_end=None)])
    with store.db:
        store.db.execute("DELETE FROM watches")
    path = write_json(tmp_path / "broad.json", {"schema": "keywordmoves-watchlist/v1",
        "watches": [watched(metric=None, evidence_kind=None)]})
    store.add_watchlist(path, now=NOW)
    item = analysis.coverage(store, now=NOW)["rows"][0]
    assert item["status"] == "current" and item["has_demand_signal"] is False
    assert item["evidence_kind"] == "language_suggestion"


def test_newly_captured_old_report_is_stale(store, tmp_path):
    ingest(store, tmp_path, [row(observed_at="2026-10-07", period_start="2026-07-01",
                               period_end="2026-07-07")])
    item = analysis.coverage(store, now=NOW)["rows"][0]
    assert item["status"] == "stale"
    assert item["capture_age_days"] < 2 and item["report_age_days"] > 90


def test_compatible_equal_nonoverlapping_periods_produce_source_labelled_alert(store, tmp_path):
    ingest(store, tmp_path, two_periods())
    report = analysis.alerts(store, now=NOW)
    assert len(report["changes"]) == 1
    item = report["changes"][0]
    assert item["delta"] == 10 and item["percent_change"] == 100
    assert item["source"] == "Google Search Console"
    assert item["evidence_kind"] == "property_performance"
    assert report["notifications_sent"] is False


@pytest.mark.parametrize("changes,reason", [
    ({"period_start": "2026-09-25", "period_end": "2026-10-01"}, "overlap"),
    ({"period_start": "2026-09-28", "period_end": "2026-10-03"}, "length"),
    ({"completeness": "partial"}, "partial"),
    ({"completeness": "unknown"}, "unknown"),
    ({"approximate": True}, "approximate"),
    ({"censored": True, "value": "<10"}, "non-numeric"),
    ({"value": None, "availability": "suppressed"}, "unavailable"),
])
def test_incompatible_data_cannot_trigger_change_alerts(store, tmp_path, changes, reason):
    earlier, later = two_periods()
    ingest(store, tmp_path, [earlier, {**later, **changes}])
    result = analysis.alerts(store, now=NOW)
    assert result["changes"] == []
    assert reason in result["blocked_comparisons"][0]["reason"]


@pytest.mark.parametrize("field,value", [
    ("platform", "Bing"), ("source", "Other provider"), ("unit", "views"),
    ("geography", "US"), ("scope", "other channel"), ("window", "30 days"),
    ("dimensions", {"device": "mobile"}),
])
def test_platform_provider_unit_geography_and_settings_never_merge(store, tmp_path, field, value):
    earlier, later = two_periods()
    ingest(store, tmp_path, [earlier, {**later, field: value}])
    assert analysis.alerts(store, now=NOW)["changes"] == []


def test_zero_baseline_has_no_invented_percentage(store, tmp_path):
    earlier, later = two_periods()
    earlier["value"] = 0
    ingest(store, tmp_path, [earlier, later])
    result = analysis.alerts(store, now=NOW)["changes"][0]
    assert result["delta"] == 20 and result["percent_change"] is None


def test_relative_indices_require_a_shared_normalisation():
    before = observation(row(evidence_kind="relative_interest", unit="index_0_100",
                             period_start=None, period_end=None, observed_at="2026-10-01"), now=NOW)
    after = observation(row(evidence_kind="relative_interest", unit="index_0_100",
                            period_start=None, period_end=None), now=NOW)
    assert "normalization" in analysis.comparable(before, after)
    before = observation({**before, "normalization_id": "reviewed-common-anchor"}, now=NOW)
    after = observation({**after, "normalization_id": "reviewed-common-anchor"}, now=NOW)
    assert analysis.comparable(before, after) is None


def test_changed_watch_preserves_prior_configuration(store, tmp_path):
    file = write_json(tmp_path / "changed.json", {"schema": "keywordmoves-watchlist/v1",
        "watches": [watched(freshness_days=10)]})
    assert store.add_watchlist(file, now=NOW)["watches_changed"] == 1
    assert store.db.execute("SELECT count(*) FROM watch_versions").fetchone()[0] == 2
    assert store.add_watchlist(file, now=NOW)["watches_changed"] == 0


def test_account_quota_is_shared_across_connections_and_does_not_refund(store):
    first = store.reserve("example-google", "same-account", request_count=3,
                          daily_runs=2, daily_requests=5, now=NOW)
    with MonitorStore(store.path) as other:
        with pytest.raises(InputError, match="running"):
            other.reserve("example-google", "same-account", request_count=1,
                          daily_runs=2, daily_requests=5, now=NOW)
        with pytest.raises(InputError, match="exhausted"):
            other.reserve("other-watch", "same-account", request_count=3,
                          daily_runs=99, daily_requests=99, now=NOW)
    store.finish(first, "error", error_kind="SyntheticFailure", now=NOW)
    assert store.health()["quotas"][0]["requests"] == 3
    with pytest.raises(InputError, match="exhausted"):
        store.reserve("example-google", "same-account", request_count=3,
                      daily_runs=2, daily_requests=5, now=NOW)


def test_prune_removes_raw_deleted_rows_from_mixed_source_snapshot(store, tmp_path):
    ingest(store, tmp_path, [row(observed_at="2026-10-01", platform="TikTok", period_start="2026-09-20", period_end="2026-09-26"),
                            row(observed_at="2026-10-07")])
    result = store.prune("2026-10-02", platform="TikTok")
    assert result["observations_deleted"] == 1
    assert len(store.observations(as_of=NOW)) == 1
    assert store.db.execute("SELECT raw FROM snapshots").fetchone()[0] is None
    assert store.verify()["verified"]


def test_tampered_snapshot_and_measurement_fail_integrity(store, tmp_path):
    ingest(store, tmp_path, [row()])
    with store.db:
        store.db.execute("UPDATE snapshots SET raw=?", (b"changed",))
    assert not store.verify()["verified"]
    with store.db:
        old = store.db.execute("SELECT payload FROM observations").fetchone()[0]
        changed = json.loads(old)
        changed["value"] = 9999
        store.db.execute("UPDATE observations SET payload=?", (json.dumps(changed),))
    assert store.verify()["observation_hash_failures"]


def test_imported_plugin_result_requires_context_and_preserves_native_units():
    payload = PluginResult("example", "import", (
        KeywordCandidate("example song", evidence=(
            KeywordEvidence("Provider", "estimated_volume", "<100", "monthly_searches",
                            "2026-10-07", "GB"),)),)).to_dict()
    with pytest.raises(InputError):
        decode_payload(payload, now=NOW)
    result = decode_payload(payload, {"subject": "Example Song", "platform": "Google",
                                      "evidence_kind": "search_volume_estimate"}, now=NOW)[0]
    assert result["value"] == "<100" and result["approximate"] is True


class FakeRegistry:
    def __init__(self, *, fail=False):
        self.calls = []
        self.fail = fail

    def get(self, name):
        return self

    def run(self, request, context):
        self.calls.append(request)
        if self.fail:
            raise InputError("Synthetic provider failure with private diagnostic text.")
        return PluginResult("google-search", "gsc-query-pages", (
            KeywordCandidate("example song", evidence=(
                KeywordEvidence("Google Search Console", "impressions", 12, "count",
                                "2026-10-08", "GB"),)),))


def collection_plan(store, tmp_path):
    watched_row = watched(dimensions={"period_days": 7, "settle_lag_days": 3},
        collector={"plugin": "google-search", "operation": "gsc-query-pages",
                   "options": {"site_url": "https://example.com/", "start_date": "{period_start}",
                               "end_date": "{period_end}"}})
    file = write_json(tmp_path / "collector-watch.json", {
        "schema": "keywordmoves-watchlist/v1", "watches": [watched_row]})
    store.add_watchlist(file, now=NOW)
    plan = analysis.due(store, now=NOW)
    plan["entries"][0]["collector"] = resolved_collector(watched_row, NOW)
    path = write_json(tmp_path / "plan.json", plan)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_collection_needs_exact_plan_opt_in_and_preserves_failure_quota(store, tmp_path):
    path, sha = collection_plan(store, tmp_path)
    fake = FakeRegistry(fail=True)
    with pytest.raises(InputError, match="allow-network"):
        collect(store, path, approved_sha256=sha, allow_network=False,
                account="approved-account", registry=fake, now=NOW)
    with pytest.raises(InputError, match="SHA-256"):
        collect(store, path, approved_sha256="0"*64, allow_network=True,
                account="approved-account", registry=fake, now=NOW)
    assert fake.calls == []
    result = collect(store, path, approved_sha256=sha, allow_network=True,
                     account="approved-account", registry=fake, now=NOW)
    assert result["runs"][0]["state"] == "error"
    assert "private diagnostic" not in json.dumps(store.health())
    assert store.health()["quotas"][0]["requests"] == 5
    assert analysis.coverage(store, now=NOW)["rows"][0]["status"] == "error"
    repeated = collect(store, path, approved_sha256=sha, allow_network=True,
                       account="approved-account", registry=fake, now=NOW)
    assert repeated["runs"][0]["state"] == "blocked"
    assert len(fake.calls) == 1


def test_successful_collector_composes_existing_reader_without_paid_fallback(store, tmp_path):
    path, sha = collection_plan(store, tmp_path)
    fake = FakeRegistry()
    result = collect(store, path, approved_sha256=sha, allow_network=True,
                     account="approved-account", registry=fake, now=NOW)
    assert result["runs"][0]["state"] == "success"
    assert fake.calls[0].options["max_requests"] == 5
    assert fake.calls[0].options["start_date"] == "2026-09-29"
    assert fake.calls[0].options["end_date"] == "2026-10-05"
    assert store.observations(as_of=NOW)[0]["value"] == 12


def test_stale_watch_plan_and_paid_route_are_rejected_before_network(store, tmp_path):
    path, _ = collection_plan(store, tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["entries"][0]["collector"]["plugin"] = "dataforseo"
    write_json(path, plan)
    fake = FakeRegistry()
    with pytest.raises(InputError, match="changed"):
        collect(store, path, approved_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                allow_network=True, account="a", registry=fake, now=NOW)
    assert fake.calls == []


def test_report_escapes_untrusted_subject_and_metric_text():
    report = {"as_of": NOW, "status_counts": {"current": 1}, "rows": [{
        "subject": '<img src=x onerror="alert(1)">', "platform": "Example",
        "phrase": "<script>unsafe()</script>", "status": "current",
        "latest": {"metric": "native text", "value": "<100", "unit": "searches",
                   "observed_at": NOW}, "evidence_kind": "search_volume_estimate"}]}
    html = analysis.render_html(report, {"changes": []})
    assert "<img src=x" not in html and "<script>unsafe()" not in html
    assert "&lt;100" in html


def test_same_capture_time_conflicts_remain_unknown(store, tmp_path):
    ingest(store, tmp_path, [row(value=10), row(value=20)])
    assert analysis.coverage(store, now=NOW)["rows"][0]["status"] == "conflicting-captures"
    result = analysis.alerts(store, now=NOW)
    assert not result["changes"]
    assert result["blocked_comparisons"][0]["reason"] == "conflicting-capture-timestamps"


def test_rolling_period_uses_most_recent_nonoverlapping_baseline(store, tmp_path):
    earlier, later = two_periods()
    overlap = row(value=15, observed_at="2026-10-03", period_start="2026-09-25",
                  period_end="2026-10-01")
    ingest(store, tmp_path, [earlier, overlap, later])
    change = analysis.alerts(store, now=NOW)["changes"][0]
    assert change["before"] == 10 and change["after"] == 20


def test_broad_watch_keeps_demand_evidence_when_latest_is_language(store, tmp_path):
    file = write_json(tmp_path / "broad-watch.json", {"schema": "keywordmoves-watchlist/v1",
        "watches": [watched(metric=None, source=None, evidence_kind=None)]})
    store.add_watchlist(file, now=NOW)
    ingest(store, tmp_path, [
        row(source="Provider", metric="search_estimate", evidence_kind="search_volume_estimate",
            observed_at="2026-10-05", value="<100", approximate=True, censored=True),
        row(source="Caption", metric="language", evidence_kind="language_suggestion",
            observed_at="2026-10-07", value="related wording")])
    output = analysis.coverage(store, now=NOW)["rows"][0]
    assert output["has_demand_signal"] is True
    assert output["last_demand_signal"]["source"] == "Provider"
    assert output["evidence_counts"]["language_suggestion"] == 1


def test_cli_end_to_end_and_original_input_overwrite_guard(tmp_path, capsys):
    from keywordmoves.cli import main
    prefix = ["monitor", "--store", str(tmp_path / "cli.sqlite")]
    assert main([*prefix, "init"]) == 0
    capsys.readouterr()
    file = write_json(tmp_path / "watch.json", {"schema": "keywordmoves-watchlist/v1",
                                             "watches": [watched()]})
    assert main([*prefix, "watch", "--input", str(file)]) == 0
    original = file.read_bytes()
    assert main([*prefix, "watch", "--input", str(file), "--output", str(file)]) == 2
    assert file.read_bytes() == original
    capsys.readouterr()
    data = write_json(tmp_path / "observations.json", {"schema": SCHEMA, "observations": two_periods()})
    assert main([*prefix, "import", "--input", str(data)]) == 0
    capsys.readouterr()
    html = tmp_path / "report.html"
    assert main([*prefix, "report", "--as-of", NOW, "--output", str(html)]) == 0
    assert "KeywordMoves evidence coverage" in html.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main([*prefix, "alerts", "--as-of", NOW]) == 0
    report = json.loads(capsys.readouterr().out)
    identifier = report["changes"][0]["id"]
    assert main([*prefix, "acknowledge", "--alert-id", identifier]) == 0
    capsys.readouterr()
    assert main([*prefix, "alerts", "--as-of", NOW]) == 0
    assert json.loads(capsys.readouterr().out)["changes"] == []


def test_synthetic_demo_is_reproducible_and_does_not_claim_live_demand(tmp_path):
    from keywordmoves.monitoring.demo import generate
    result = generate(tmp_path / "demo")
    assert result["synthetic"] and result["network_requests"] == 0
    coverage = json.loads((tmp_path / "demo/coverage.json").read_text(encoding="utf-8"))
    assert coverage["watch_count"] == 12 and coverage["status_counts"]["missing"] == 7
    assert "invented sample data" in (tmp_path / "demo/coverage.html").read_text(encoding="utf-8")


def test_capture_verification_respects_the_permitted_root_and_reports_mismatch(tmp_path):
    from keywordmoves.monitoring.captures import verify_captures
    root = tmp_path / "permitted"
    root.mkdir()
    capture = root / "capture.txt"
    capture.write_bytes(b"actual capture")
    other = tmp_path / "outside.txt"
    other.write_bytes(b"private")
    expected = hashlib.sha256(capture.read_bytes()).hexdigest()
    result = verify_captures([
        {"capture_file": str(capture), "capture_sha256": expected},
        {"capture_file": str(capture), "capture_sha256": "0"*64},
        {"capture_file": str(other), "capture_sha256": "0"*64},
    ], root)
    assert result["status_counts"] == {
        "verified": 1, "hash-mismatch": 1, "outside-permitted-root": 1}
    assert not result["all_references_verified"]
    assert "actual_sha256" not in result["rows"][2]


def test_live_reader_inventory_matches_actual_registered_operations():
    from keywordmoves.monitoring.runner import READ_OPERATIONS
    from keywordmoves.registry import PluginRegistry
    registry = PluginRegistry()
    for plugin, operations in READ_OPERATIONS.items():
        assert operations <= set(registry.get(plugin).descriptor.operations)


def test_malformed_stored_payload_fails_integrity_without_hiding_failure(store, tmp_path):
    ingest(store, tmp_path, [row()])
    store.db.execute("UPDATE observations SET payload='not-json'")
    store.db.commit()
    result = store.verify()
    assert not result["verified"] and result["observation_hash_failures"]


def test_cli_capture_mismatch_is_non_success_even_with_healthy_database(tmp_path, capsys):
    from keywordmoves.cli import main

    capture = tmp_path / "capture.txt"
    capture.write_bytes(b"actual bytes")
    with MonitorStore(tmp_path / "verify.sqlite", create=True) as instance:
        ingest(instance, tmp_path, [row(capture_file=str(capture), capture_sha256="0"*64)])
    assert main(["monitor", "--store", str(tmp_path / "verify.sqlite"), "verify",
                 "--capture-root", str(tmp_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["database_verified"] and not result["verified"]
    assert result["captures"]["status_counts"] == {"hash-mismatch": 1}


def test_reviewed_legacy_labels_keep_language_and_unavailable_separate(tmp_path):
    legacy = {"schema": "keywordmoves-observations/v1", "observations": [
        row(evidence_kind="native-search-sample", availability="sampled-language",
            value="Sampled caption wording", limitations="No measured demand", seed_keyword="example"),
        row(evidence_kind="native-autocomplete", availability="unavailable",
            value="Access blocked; no result retained", capture_file="unavailable",
            capture_sha256="unavailable"),
        row(evidence_kind="third-party-estimate", value="Volume <100; KD Easy"),
    ]}
    converted = decode_payload(legacy, now=NOW)
    assert converted[0]["evidence_kind"] == "language_suggestion"
    assert converted[0]["availability"] == "observed"
    assert converted[0]["dimensions"]["seed_keyword"] == "example"
    assert "No measured demand" in converted[0]["notes"]
    assert converted[1]["value"] is None and converted[1]["availability"] == "unavailable"
    assert "Access blocked" in converted[1]["notes"]
    assert converted[1]["capture_file"] is None and converted[1]["capture_sha256"] is None
    assert "Original capture_sha256: unavailable" in converted[1]["notes"]
    assert converted[2]["approximate"] and converted[2]["censored"]
    assert converted[2]["value"] == "Volume <100; KD Easy"
    canonical = {"schema": SCHEMA, "observations": [legacy["observations"][1]]}
    with pytest.raises(InputError):
        decode_payload(canonical, now=NOW)


def test_broad_html_report_shows_actual_phrase_and_uncertain_completeness(store, tmp_path):
    file = write_json(tmp_path / "broad-html.json", {"schema": "keywordmoves-watchlist/v1",
        "watches": [watched(phrase=None)]})
    store.add_watchlist(file, now=NOW)
    ingest(store, tmp_path, [row(phrase="a visible example", completeness="unknown")])
    html = analysis.render_html(analysis.coverage(store, now=NOW), {"changes": []})
    assert "<td>a visible example</td>" in html
    assert "<th>Completeness</th>" in html and "<td>unknown</td>" in html
    assert "Current means capture freshness" in html
