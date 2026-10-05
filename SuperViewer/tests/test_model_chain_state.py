"""Saved model chain file: validation, round trip, broken files."""
import json

from SuperViewer.superviewer.model_chain_state import MAX_STAGES, ModelChainStore, normalize


def test_normalize_keeps_valid_windows_and_defaults_the_rest() -> None:
    state = normalize({"auto": False, "stages": [
        {"model": "sam2.1_t.pt", "input": "trace"},
        {"model": "auto", "input": "bogus", "use": "mask", "margin": 999, "imgsz": "big", "min_conf": 0,
         "birds_only": False, "lift": "yes", "scope": "view"},
        {"model": "../evil.pt"}, {"model": 3}, "junk"]})
    assert state["auto"] is False and len(state["stages"]) == 2
    sam, det = state["stages"]
    assert sam == {"model": "sam2.1_t.pt", "input": "trace", "scope": "full", "use": "crop", "margin": 30,
                   "imgsz": 640, "min_conf": 10, "birds_only": True, "lift": True, "floating": False, "geometry": None}
    assert det["input"] is None and det["use"] == "mask" and det["scope"] == "view"
    assert (det["margin"], det["imgsz"], det["min_conf"], det["birds_only"], det["lift"]) == (200, 640, 1, False, True)
    assert len(normalize({"stages": [{"model": "auto"}] * 50})["stages"]) == MAX_STAGES
    assert normalize(None) == normalize({"stages": "x", "auto": "y"}) == {"version": 1, "auto": True, "stages": []}


def test_store_round_trips_through_a_non_ascii_folder(tmp_path) -> None:
    store = ModelChainStore(tmp_path / "用户 数据" / "model_chain.json")
    assert store.load()["stages"] == []  # no file yet
    saved = store.save({"auto": True, "stages": [{"model": "yolo11x-seg.pt", "input": "previous", "imgsz": 1024}]})
    assert store.load() == saved and saved["stages"][0]["imgsz"] == 1024
    assert [p.name for p in store.path.parent.iterdir()] == ["model_chain.json"]  # no temp file left
    store.path.write_text("{broken", encoding="utf-8")
    assert store.load()["stages"] == []
    store.path.write_text(json.dumps([1, 2]), encoding="utf-8")
    assert store.load()["stages"] == []


def test_floating_windows_keep_a_checked_geometry() -> None:
    stages = normalize({"stages": [
        {"model": "auto", "floating": True, "geometry": [100, -20, 400, 700]},
        {"model": "auto", "floating": True, "geometry": [1, 2, 3]},           # malformed → default place
        {"model": "auto", "floating": True, "geometry": [0, 0, 50, 700]},     # too small
        {"model": "auto", "floating": True, "geometry": [0, 0, True, 700]},
        {"model": "auto", "floating": False, "geometry": [100, 100, 400, 700]},  # docked: no geometry
        {"model": "auto", "floating": "yes"}]})["stages"]
    assert [(s["floating"], s["geometry"]) for s in stages] == [
        (True, [100, -20, 400, 700]), (True, None), (True, None), (True, None), (False, None), (False, None)]
