import io
import json

import pytest

from keywordmoves.builtin.pixelcue import MAX_RESPONSE_BYTES
from keywordmoves.cli import main


def test_pixelcue_cli_retains_evidence_and_keyword_only_output(tmp_path, monkeypatch, capsys):
    source = tmp_path / "cover.png"
    source.write_bytes(b"synthetic fixture")
    calls = []

    def local_response(request, timeout):
        calls.append((request.full_url, request.method, json.loads(request.data), timeout))
        return io.BytesIO(json.dumps([" Night Sky ", "night sky", "silhouette", "  "]).encode())

    monkeypatch.setattr("keywordmoves.builtin.pixelcue._LOCAL_HTTP.open", local_response)
    args = [
        "run", "pixelcue", "--operation", "extract", "--input", str(source),
        "--option", "model=fixture", "--option", "request_id=test-request",
        "--option", "limit=2", "--option", "timeout=12",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["keywords"][0]["evidence"][0]["metric"] == "vlm_generated_tag"
    assert result["metadata"]["source_file"] == str(source.resolve())
    assert calls[0] == (
        "http://127.0.0.1:8765/v1/keywords",
        "POST",
        {"path": str(source.resolve()), "model": "fixture", "request_id": "test-request"},
        12,
    )
    assert main(args + ["--format", "keywords"]) == 0
    assert json.loads(capsys.readouterr().out) == ["Night Sky", "silhouette"]


@pytest.mark.parametrize("option", [
    "endpoint=https://example.org",
    "endpoint=http://user:secret@localhost:8765",
    "endpoint=http://localhost:99999",
    "endpoint=http://localhost:8765/path",
    "timeout=nan",
    "timeout=inf",
    "limit=1.5",
])
def test_pixelcue_rejects_invalid_options_before_transport(option, tmp_path, monkeypatch, capsys):
    source = tmp_path / "cover.png"
    source.write_bytes(b"synthetic fixture")

    def unexpected_request(*args, **kwargs):
        pytest.fail("Invalid options reached transport")

    monkeypatch.setattr("keywordmoves.builtin.pixelcue._LOCAL_HTTP.open", unexpected_request)
    assert main([
        "run", "pixelcue", "--operation", "extract", "--input", str(source), "--option", option,
    ]) == 2
    assert "PixelCue" in capsys.readouterr().err


@pytest.mark.parametrize(("response", "message"), [
    (b'{"tags": ["invalid shape"]}', "list of strings"),
    (b"\xff", "malformed UTF-8 JSON"),
    (b" " * (MAX_RESPONSE_BYTES + 1), "exceeds 1 MiB"),
], ids=["shape", "utf8", "size"])
def test_pixelcue_rejects_invalid_responses(response, message, tmp_path, monkeypatch, capsys):
    source = tmp_path / "cover.png"
    source.write_bytes(b"synthetic fixture")
    monkeypatch.setattr(
        "keywordmoves.builtin.pixelcue._LOCAL_HTTP.open",
        lambda request, timeout: io.BytesIO(response),
    )
    assert main(["run", "pixelcue", "--operation", "extract", "--input", str(source)]) == 2
    assert message in capsys.readouterr().err
