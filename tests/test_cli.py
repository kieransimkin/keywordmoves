import json
from pathlib import Path

from keywordmoves.cli import main

FIXTURE = Path(__file__).parent / "fixtures" / "lyrics.txt"


def test_plugins_json_lists_both_plugin_kinds(capsys):
    assert main(["plugins", "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert {item["kind"] for item in output} == {"keyword", "llm"}


def test_cli_runs_local_text_extraction(capsys):
    assert main(
        [
            "run",
            "text-library",
            "--operation",
            "extract-local",
            "--input",
            str(FIXTURE),
            "--option",
            "limit=3",
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["plugin"] == "text-library"
    assert len(output["keywords"]) == 3


def test_cli_returns_two_for_plugin_error(capsys):
    assert main(["run", "missing", "--operation", "anything"]) == 2
    assert "was not found" in capsys.readouterr().err

