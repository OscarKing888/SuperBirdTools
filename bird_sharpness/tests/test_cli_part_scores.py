"""CLI keeps the primary result and exposes the three supplementary scores."""
import json

import pytest

from bird_sharpness import __main__ as cli
from bird_sharpness import analyzer as analyzer_mod
from bird_sharpness.analyzer import BirdSharpnessResult


@pytest.mark.parametrize("scores", [(0, 501, 250.5), (None, 501, None), (None, None, None)])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_displays_part_scores_without_replacing_primary_score(tmp_path, monkeypatch, capsys, scores, as_json):
    source = tmp_path / "中文鸟.jpg"
    source.write_bytes(b"stub")
    result = BirdSharpnessResult(str(source), "sharp", score=910, sigma=0.6, region="bird", bird_count=1,
                                head_score=scores[0], body_score=scores[1], mean_score=scores[2])

    class StubAnalyzer:
        def __init__(self, **_kwargs):
            pass

        def release(self):
            pass

    def analyze_paths(paths, *, on_result, **_kwargs):
        assert paths == [str(source)]
        on_result(1, 1, result)
        return [result]

    monkeypatch.setattr(analyzer_mod, "BirdSharpnessAnalyzer", StubAnalyzer)
    monkeypatch.setattr(analyzer_mod, "analyze_paths", analyze_paths)
    assert cli.main((["--json"] if as_json else []) + [str(source)]) == 0
    output = capsys.readouterr().out
    if as_json:
        row = json.loads(output)
        assert row["score"] == 910 and row["verdict"] == "sharp"
        assert (row["head_score"], row["body_score"], row["mean_score"]) == scores
    else:
        assert "中文鸟.jpg" in output and "score=910" in output and "清晰" in output
        for name, value in zip(("head_score", "body_score", "mean_score"), scores):
            text = "—" if value is None else format(value, ".1f" if name == "mean_score" else ".0f")
            assert f"{name}={text}" in output
