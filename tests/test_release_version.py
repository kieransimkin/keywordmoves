from pathlib import Path

import keywordmoves
from keywordmoves.cli import main


def test_cli_and_package_version_agree(capsys):
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert capsys.readouterr().out.strip() == f"keywordmoves {keywordmoves.__version__}"


def test_distribution_and_package_version_agree():
    project = Path(__file__).parents[1] / "pyproject.toml"
    assert f'version = "{keywordmoves.__version__}"' in project.read_text(encoding="utf-8")
