"""Verify the browser helper and its regression test survive wheel/sdist builds."""
import hashlib
import json
import sys
import tarfile
import zipfile
from pathlib import Path


def verify(directory):
    root = Path(__file__).resolve().parents[1]
    wheels, sdists = list(Path(directory).glob("keywordmoves-*.whl")), list(Path(directory).glob("keywordmoves-*.tar.gz"))
    assert len(wheels) == len(sdists) == 1, "Expected one wheel and one source distribution."
    relative = "src/keywordmoves/browser/free_public.mjs"
    helper = (root / relative).read_bytes()
    test = (root / "tests/browser_free_public.test.mjs").read_bytes()
    with zipfile.ZipFile(wheels[0]) as package:
        assert package.read("keywordmoves/browser/free_public.mjs") == helper
        assert package.read("keywordmoves/browser_capture.py") == (root / "src/keywordmoves/browser_capture.py").read_bytes()
    with tarfile.open(sdists[0], "r:gz") as package:
        prefix = sdists[0].name[:-7] + "/"
        assert package.extractfile(prefix + relative).read() == helper
        assert package.extractfile(prefix + "tests/browser_free_public.test.mjs").read() == test
    result = {"browser_helper_sha256": hashlib.sha256(helper).hexdigest(),
        "distributions": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [*wheels, *sdists]}}
    print(json.dumps(result))


if __name__ == "__main__":
    verify(sys.argv[1])
