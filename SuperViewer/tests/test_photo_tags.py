from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from app_common.exif_io.json_sidecar import JSON_SIDECAR_SUFFIX, json_sidecar_path_for
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from app_common.exif_io.xmp_sidecar_edits import (
    edit_dir_for,
    legacy_edit_dir_for,
    sidecar_sha256,
    write_edit_file,
)
from app_common.exif_io.writer import read_batch_metadata
from SuperViewer.superviewer.photo_tags import (
    PhotoTagConfig,
    PhotoTagSidecarStore,
    TagTreeNode,
    find_superpicky_tag_config_path,
    iter_tag_tree_leaves,
    parse_tag_tree_text,
    photo_tag_filter_matches,
)
from SuperViewer.superviewer.tagged_file_list import (
    SuperViewerTaggedFileListPanel,
    filter_text_tokens_match,
)
from SuperViewer.superviewer.qt_compat import QApplication


def test_photo_tag_config_loads_utf8_lines_and_dedupes(tmp_path: Path) -> None:
    cfg = tmp_path / "tags.cfg"
    cfg.write_text("窝\n打架\n\n打架\n捕食\n", encoding="utf-8")

    assert PhotoTagConfig(cfg).load() == ["窝", "打架", "捕食"]


def test_photo_tag_config_parses_indent_tree_leaves_only(tmp_path: Path) -> None:
    cfg = tmp_path / "tags.cfg"
    cfg.write_text(
        "# comment\n"
        "MVP高光时刻\n"
        "行为\n"
        "  窝\n"
        "  打架\n"
        "  打架\n"
        "场景\n"
        "  同框\n"
        "\n"
        "  炮弹\n",
        encoding="utf-8",
    )

    tree, tags = PhotoTagConfig(cfg).load_tree_and_tags()
    assert tags == ["MVP高光时刻", "窝", "打架", "同框", "炮弹"]
    assert [node.name for node in tree] == ["MVP高光时刻", "行为", "场景"]
    assert tree[0].is_leaf
    assert tree[1].is_group
    assert [child.name for child in tree[1].children] == ["窝", "打架"]
    assert [child.name for child in tree[2].children] == ["同框", "炮弹"]
    assert PhotoTagConfig(cfg).load() == tags


def test_parse_tag_tree_text_supports_tabs_and_nested_groups() -> None:
    tree = parse_tag_tree_text(
        "根\n"
        "\t一级\n"
        "\t\t叶子A\n"
        "\t叶子B\n"
    )
    assert iter_tag_tree_leaves(tree) == ["叶子A", "叶子B"]
    assert tree[0].name == "根"
    assert tree[0].children[0].name == "一级"
    assert tree[0].children[0].children[0].name == "叶子A"
    assert tree[0].children[1].name == "叶子B"


def test_tag_tree_node_helpers() -> None:
    leaf = TagTreeNode(name="窝")
    group = TagTreeNode(name="行为", children=[leaf])
    assert leaf.is_leaf and not leaf.is_group
    assert group.is_group and not group.is_leaf


def test_photo_tag_config_path_comes_from_nearest_superpicky(tmp_path: Path) -> None:
    root = tmp_path / "root"
    leaf = root / "nested" / "leaf"
    superpicky = root / ".superpicky"
    leaf.mkdir(parents=True)
    superpicky.mkdir()
    (superpicky / "tags.cfg").write_text("alpha\nbeta\n", encoding="utf-8")

    assert find_superpicky_tag_config_path(leaf) == superpicky / "tags.cfg"
    assert PhotoTagConfig(find_superpicky_tag_config_path(leaf)).load() == ["alpha", "beta"]

    nested_superpicky = leaf / ".superpicky"
    nested_superpicky.mkdir()
    assert find_superpicky_tag_config_path(leaf) == nested_superpicky / "tags.cfg"
    assert find_superpicky_tag_config_path(nested_superpicky) == nested_superpicky / "tags.cfg"
    outside = tmp_path / "outside"
    outside.mkdir()
    assert find_superpicky_tag_config_path(outside, max_levels=0) is None


def test_photo_tag_filter_exact_match_requires_equal_tag() -> None:
    assert photo_tag_filter_matches(["鸟"], ["鸟"])
    assert not photo_tag_filter_matches(["鸟"], ["水鸟"])


def test_photo_tag_filter_partial_match_allows_substring() -> None:
    assert photo_tag_filter_matches(["鸟"], ["水鸟"], partial_match=True)


def test_photo_tag_filter_exact_multiple_filters_require_all_matches() -> None:
    assert photo_tag_filter_matches(["标签1", "标签2"], ["标签1", "标签2"])
    assert photo_tag_filter_matches(["标签1", "标签2"], ["标签1", "标签2", "标签3"])
    assert not photo_tag_filter_matches(["标签1", "标签2"], ["标签1", "标签3"])
    assert not photo_tag_filter_matches(["鸟", "猛禽"], ["昆虫"])


def test_photo_tag_filter_partial_multiple_filters_accept_any_match() -> None:
    assert photo_tag_filter_matches(["鸟", "猛禽"], ["水鸟"], partial_match=True)
    assert photo_tag_filter_matches(["鸟", "猛禽"], ["猛禽类"], partial_match=True)
    assert not photo_tag_filter_matches(["鸟", "猛禽"], ["昆虫"], partial_match=True)


def test_filter_text_tokens_match_single_tag_substring() -> None:
    assert filter_text_tokens_match(["打架"], photo_tags=["打架", "捕食"])
    assert filter_text_tokens_match(["打"], photo_tags=["打架"])
    assert not filter_text_tokens_match(["炮弹"], photo_tags=["打架"])


def test_filter_text_tokens_match_multiple_tags_require_all() -> None:
    assert filter_text_tokens_match(["打架", "捕食"], photo_tags=["打架", "捕食", "窝"])
    assert not filter_text_tokens_match(["打架", "捕食"], photo_tags=["打架"])


def test_filter_text_tokens_match_filename_or_comment() -> None:
    assert filter_text_tokens_match(["DSC"], name="DSC06705.jpg", comment="", photo_tags=[])
    assert filter_text_tokens_match(["nest"], name="a.jpg", comment="nesting pair", photo_tags=[])
    assert filter_text_tokens_match(
        ["DSC", "打架"],
        name="DSC06705.jpg",
        comment="",
        photo_tags=["打架"],
    )
    assert not filter_text_tokens_match(
        ["DSC", "打架"],
        name="DSC06705.jpg",
        comment="",
        photo_tags=["捕食"],
    )


def test_path_matches_active_filters_text_tokens_use_tags_and_meta() -> None:
    tagged = os.path.normpath("C:/photos/tagged.jpg")
    named = os.path.normpath("C:/photos/DSC06705.jpg")
    commented = os.path.normpath("C:/photos/other.jpg")
    plain = os.path.normpath("C:/photos/plain.jpg")

    class _FilterEdit:
        def __init__(self, text: str) -> None:
            self._text = text

        def text(self) -> str:
            return self._text

    class _Harness:
        _path_matches_active_filters = SuperViewerTaggedFileListPanel._path_matches_active_filters
        _filter_text_tokens = SuperViewerTaggedFileListPanel._filter_text_tokens
        _photo_tags_for_filter_path = SuperViewerTaggedFileListPanel._photo_tags_for_filter_path
        _photo_tags_from_meta_cache = SuperViewerTaggedFileListPanel._photo_tags_from_meta_cache

        def __init__(self) -> None:
            self._filter_edit = _FilterEdit("打架 捕食")
            self._filter_pick = False
            self._filter_reject = False
            self._filter_min_rating = 0
            self._filter_focus_status = ""
            self._active_tag_filters: set[str] = set()
            self._tag_filter_partial_match = True
            self._photo_tag_cache = {tagged: {"打架", "捕食"}}
            self._meta_cache = {
                commented: {"tags": ["打架", "捕食"], "comment": "ignore"},
                named: {"comment": "some note"},
                plain: {},
            }

        def _path_matches_filters(self, path: str, **kwargs) -> bool:
            # Mimic base pick/rating path with empty text filter.
            assert kwargs.get("filter_text") == ""
            return bool(path)

    harness = _Harness()
    assert harness._path_matches_active_filters(tagged)
    assert harness._path_matches_active_filters(commented)
    assert not harness._path_matches_active_filters(named)
    assert not harness._path_matches_active_filters(plain)

    harness._filter_edit = _FilterEdit("DSC")
    assert harness._path_matches_active_filters(named)
    assert not harness._path_matches_active_filters(tagged)

    harness._filter_edit = _FilterEdit("DSC 打架")
    harness._photo_tag_cache[named] = {"打架"}
    assert harness._path_matches_active_filters(named)
    assert not harness._path_matches_active_filters(tagged)


def test_xmp_subject_roundtrip_preserves_multiple_tags(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()

    assert metadata.write_subjects(str(photo_path), ["打架", "捕食", "打架"])
    assert metadata.read_subjects(str(photo_path)) == ["打架", "捕食"]
    assert (tmp_path / "img001.xmp").is_file()

    flat = metadata.read(str(photo_path))
    assert flat.get("XMP-dc:subject") == "打架; 捕食"
    assert flat.get("XMP-dc:Subject") == "打架; 捕食"


def test_pending_sidecar_edits_merge_and_compact_on_read(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    metadata = PhotoMetaDataXMP()

    assert metadata.write_subjects(str(photo_path), ["base"])
    sidecar_path = tmp_path / "img001.xmp"
    assert write_edit_file(
        sidecar_path,
        photo_path,
        [{"field": "subject", "op": "add", "values": ["alpha"]}],
        base_hash=sidecar_sha256(sidecar_path),
    )
    assert write_edit_file(
        sidecar_path,
        photo_path,
        [{"field": "subject", "op": "add", "values": ["beta"]}],
        base_hash=sidecar_sha256(sidecar_path),
    )

    assert metadata.read_subjects(str(photo_path)) == ["base", "alpha", "beta"]
    assert not list(edit_dir_for(sidecar_path).glob("*.json"))
    assert metadata.read(str(photo_path)).get("XMP-dc:subject") == "base; alpha; beta"


def test_sidecar_edit_dir_lives_under_superpicky_and_disambiguates_same_names(tmp_path: Path) -> None:
    root = tmp_path / "library"
    superpicky = root / ".superpicky"
    first_dir = root / "day1"
    second_dir = root / "day2"
    first_dir.mkdir(parents=True)
    second_dir.mkdir(parents=True)
    superpicky.mkdir()
    first_sidecar = first_dir / "DSC06705.xmp"
    second_sidecar = second_dir / "DSC06705.xmp"

    first_edit_dir = edit_dir_for(first_sidecar)
    second_edit_dir = edit_dir_for(second_sidecar)

    assert os.path.commonpath([str(superpicky), str(first_edit_dir)]) == str(superpicky)
    assert os.path.commonpath([str(superpicky), str(second_edit_dir)]) == str(superpicky)
    assert first_edit_dir.parts[-3] == "sidecar_edits"
    assert second_edit_dir.parts[-3] == "sidecar_edits"
    assert first_edit_dir != second_edit_dir
    assert legacy_edit_dir_for(first_sidecar) == first_dir / "DSC06705.xmp.superpicky-edits"

    assert write_edit_file(
        first_sidecar,
        first_dir / "DSC06705.jpg",
        [{"field": "subject", "op": "add", "values": ["alpha"]}],
    )
    assert list(first_edit_dir.glob("*.json"))
    assert not legacy_edit_dir_for(first_sidecar).exists()


def test_xmp_write_accepts_subject_field_alias(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo_path), {"XMP-dc:Subject": "打架; 捕食"})
    assert metadata.read_subjects(str(photo_path)) == ["打架", "捕食"]


def test_xmp_rating_pick_read_as_normalized_fields(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()

    assert metadata.write(str(photo_path), {"XMP-xmp:Rating": 3, "XMP-xmpDM:pick": 1})
    flat = metadata.read(str(photo_path))

    assert flat.get("XMP-xmp:Rating") == "3"
    assert flat.get("rating") == 3
    assert flat.get("XMP-xmpDM:pick") == "1"
    assert flat.get("pick") == 1


def test_xmp_write_invalidates_batch_metadata_cache(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")
    norm_path = os.path.normpath(str(photo_path))

    metadata = PhotoMetaDataXMP()
    before = read_batch_metadata([str(photo_path)]).get(norm_path, {})
    assert before.get("XMP-xmp:Rating") in (None, "")

    assert metadata.write(str(photo_path), {"XMP-xmp:Rating": 4})
    after = read_batch_metadata([str(photo_path)]).get(norm_path, {})

    assert after.get("XMP-xmp:Rating") == "4"
    assert after.get("rating") == 4


def test_store_persists_multiple_tags_per_photo_in_sidecar(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    store = PhotoTagSidecarStore()
    store.set_tag_for_paths([str(photo_path)], "打架", True)
    store.set_tag_for_paths([str(photo_path)], "捕食", True)
    assert store.get_tags(str(photo_path)) == {"打架", "捕食"}

    store.set_tag_for_paths([str(photo_path)], "打架", False)
    assert store.get_tags(str(photo_path)) == {"捕食"}


def test_store_persists_tags_to_central_json_sidecar_under_superpicky(tmp_path: Path) -> None:
    root = tmp_path / "library"
    photo_dir = root / "day1"
    photo_dir.mkdir(parents=True)
    (root / ".superpicky").mkdir()
    photo_path = photo_dir / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    store = PhotoTagSidecarStore()
    store.set_tag_for_paths([str(photo_path)], "alpha", True)

    expected = root / ".superpicky" / "metadata" / "day1" / f"img001.jpg{JSON_SIDECAR_SUFFIX}"
    assert json_sidecar_path_for(str(photo_path)) == expected
    assert expected.is_file()
    assert store.get_tags(str(photo_path)) == {"alpha"}


def test_store_loads_and_filters_configured_tags(tmp_path: Path) -> None:
    p1 = tmp_path / "a.jpg"
    p2 = tmp_path / "b.jpg"
    p1.write_bytes(b"")
    p2.write_bytes(b"")

    store = PhotoTagSidecarStore()
    store.set_tag_for_paths([str(p1), str(p2)], "同框", True)
    store.set_tag_for_paths([str(p2)], "踩背", True)

    tags = store.load_tags_for_paths([str(p1), str(p2)], allowed_tags=["同框"])
    assert tags[str(p1)] == {"同框"}
    assert tags[str(p2)] == {"同框"}


def test_clear_configured_tags_preserves_unrelated_xmp_subjects(tmp_path: Path) -> None:
    photo_path = tmp_path / "img001.jpg"
    photo_path.write_bytes(b"not an image")

    metadata = PhotoMetaDataXMP()
    assert metadata.write_subjects(str(photo_path), ["Lightroom", "打架", "捕食"])

    store = PhotoTagSidecarStore(metadata)
    store.clear_tags_for_paths([str(photo_path)], allowed_tags=["打架", "捕食"])

    assert metadata.read_subjects(str(photo_path)) == ["Lightroom"]


class _TagCacheHarness:
    _photo_tags_from_meta_cache = SuperViewerTaggedFileListPanel._photo_tags_from_meta_cache
    _meta_cache_has_photo_tags = SuperViewerTaggedFileListPanel._meta_cache_has_photo_tags
    _seed_photo_tag_cache_from_meta = SuperViewerTaggedFileListPanel._seed_photo_tag_cache_from_meta
    _tags_for_path = SuperViewerTaggedFileListPanel._tags_for_path

    def __init__(self) -> None:
        self._available_tags = ["打架", "捕食"]
        self._photo_tag_cache: dict[str, set[str]] = {}
        self._meta_cache: dict[str, dict] = {}
        self._all_files: list[str] = []
        self.queued: list[str] = []

    def _queue_photo_tag_lookup(self, path: str) -> None:
        self.queued.append(path)


def test_tag_lookup_prefers_metadata_cache_and_caches_known_empty_values() -> None:
    empty_path = os.path.normpath("C:/photos/empty.jpg")
    tagged_path = os.path.normpath("C:/photos/tagged.jpg")
    harness = _TagCacheHarness()
    harness._all_files = [empty_path, tagged_path]
    harness._meta_cache = {
        empty_path: {"tags": []},
        tagged_path: {"tags": ["打架", "未配置"]},
    }

    assert harness._tags_for_path(empty_path) == set()
    assert empty_path in harness._photo_tag_cache
    assert harness._tags_for_path(empty_path) == set()
    assert harness._tags_for_path(tagged_path) == {"打架"}
    assert harness.queued == []


def test_tag_lookup_cache_miss_queues_async_work_without_sync_store_read() -> None:
    path = os.path.normpath("C:/photos/missing.jpg")
    harness = _TagCacheHarness()

    assert harness._tags_for_path(path) == set()
    assert harness.queued == [path]
    assert path not in harness._photo_tag_cache


def test_on_demand_tag_lookup_builds_bounded_same_directory_async_batch() -> None:
    current = os.path.normpath("C:/photos/current.jpg")
    known_meta = os.path.normpath("C:/photos/known-meta.jpg")
    known_cache = os.path.normpath("C:/photos/known-cache.jpg")
    same_dir = [os.path.normpath(f"C:/photos/{index:04d}.jpg") for index in range(400)]
    other_dir = os.path.normpath("C:/other/other.jpg")

    class _QueueHarness:
        _queue_photo_tag_lookup = SuperViewerTaggedFileListPanel._queue_photo_tag_lookup
        _meta_cache_has_photo_tags = SuperViewerTaggedFileListPanel._meta_cache_has_photo_tags

        def __init__(self) -> None:
            self._tag_shutdown_requested = False
            self._all_files = [known_meta, known_cache, *same_dir, other_dir]
            self._photo_tag_cache = {known_cache: set()}
            self._meta_cache = {known_meta: {"tags": []}}
            self.calls: list[tuple[list[str], str, bool]] = []

        def _start_photo_tag_cache_loader_if_needed(
            self,
            paths,
            *,
            reason: str,
            allow_without_filters: bool = False,
        ) -> None:
            self.calls.append((list(paths), reason, allow_without_filters))

    harness = _QueueHarness()
    harness._queue_photo_tag_lookup(current)

    assert len(harness.calls) == 1
    batch, reason, allow_without_filters = harness.calls[0]
    assert batch[0] == current
    assert len(batch) == 256
    assert known_meta not in batch
    assert known_cache not in batch
    assert other_dir not in batch
    assert reason == "on_demand"
    assert allow_without_filters is True


def test_tag_loader_pointer_merges_pending_until_real_thread_finished() -> None:
    first = os.path.normpath("C:/photos/first.jpg")
    second = os.path.normpath("C:/photos/second.jpg")
    third = os.path.normpath("C:/photos/third.jpg")

    class _RunningWorker:
        @staticmethod
        def isRunning() -> bool:
            raise AssertionError("start path must not poll a worker whose finished slot is pending")

    class _PendingHarness:
        _start_photo_tag_cache_loader_if_needed = (
            SuperViewerTaggedFileListPanel._start_photo_tag_cache_loader_if_needed
        )
        _photo_tags_from_meta_cache = SuperViewerTaggedFileListPanel._photo_tags_from_meta_cache
        _meta_cache_has_photo_tags = SuperViewerTaggedFileListPanel._meta_cache_has_photo_tags
        _seed_photo_tag_cache_from_meta = SuperViewerTaggedFileListPanel._seed_photo_tag_cache_from_meta
        _on_photo_tag_cache_finished = SuperViewerTaggedFileListPanel._on_photo_tag_cache_finished

        def __init__(self) -> None:
            self._tag_shutdown_requested = False
            self._available_tags = ["打架"]
            self._active_tag_filters: set[str] = set()
            self._photo_tag_cache: dict[str, set[str]] = {}
            self._meta_cache: dict[str, dict] = {}
            self._all_files: list[str] = []
            self._photo_tag_pending_paths: list[str] = []
            self._photo_tag_loader = _RunningWorker()
            self._photo_tag_stopping_loader = None
            self._photo_tag_cache_done = 0
            self._photo_tag_cache_total = 0

    harness = _PendingHarness()
    harness._start_photo_tag_cache_loader_if_needed(
        [first, second],
        reason="on_demand",
        allow_without_filters=True,
    )
    harness._start_photo_tag_cache_loader_if_needed(
        [second, third],
        reason="on_demand",
        allow_without_filters=True,
    )

    assert harness._photo_tag_pending_paths == [first, second, third]
    worker = harness._photo_tag_loader
    harness._on_photo_tag_cache_finished(worker, 2, 3)
    assert harness._photo_tag_loader is worker
    assert (harness._photo_tag_cache_done, harness._photo_tag_cache_total) == (2, 3)


def test_late_tag_worker_batch_cannot_overwrite_newer_write_cache() -> None:
    path = os.path.normpath("C:/photos/race.jpg")
    key = os.path.normcase(path)

    class _Signal:
        def __init__(self) -> None:
            self.values: list[list[str]] = []

        def emit(self, values) -> None:
            self.values.append(list(values))

    class _Worker:
        _tag_generation_snapshot = {key: 0}

    class _RaceHarness:
        _photo_tag_generation = SuperViewerTaggedFileListPanel._photo_tag_generation
        _bump_photo_tag_generations = SuperViewerTaggedFileListPanel._bump_photo_tag_generations
        _sync_photo_tags_to_meta_cache = SuperViewerTaggedFileListPanel._sync_photo_tags_to_meta_cache
        _on_photo_tag_cache_batch_ready = (
            SuperViewerTaggedFileListPanel._on_photo_tag_cache_batch_ready
        )

        def __init__(self) -> None:
            self._available_tags = ["打架", "捕食"]
            self._photo_tag_cache = {path: {"捕食"}}
            self._photo_tag_generation_by_path: dict[str, int] = {}
            self._meta_cache = {path: {"tags": ["捕食"]}}
            self._photo_tag_loader = _Worker()
            self.metadata_cache_updated = _Signal()

        def _schedule_photo_tag_filter_refresh(self) -> None:
            raise AssertionError("stale worker batch must not schedule a filter refresh")

    harness = _RaceHarness()
    # 模拟 worker 启动后，GUI 完成一次更新并把 cache 刷成新值。
    harness._bump_photo_tag_generations([path])
    harness._on_photo_tag_cache_batch_ready(
        harness._photo_tag_loader,
        {path: {"打架"}},
    )

    assert harness._photo_tag_cache[path] == {"捕食"}
    assert harness._meta_cache[path]["tags"] == ["捕食"]
    assert harness.metadata_cache_updated.values == []


def test_late_metadata_batch_keeps_newer_photo_tag_cache_value() -> None:
    path = os.path.normpath("C:/photos/metadata-race.jpg")

    class _Harness:
        _merge_metadata_batch_with_photo_tag_cache = (
            SuperViewerTaggedFileListPanel._merge_metadata_batch_with_photo_tag_cache
        )

        def __init__(self) -> None:
            self._available_tags = ["打架", "捕食"]
            self._photo_tag_cache = {path: {"捕食"}}

    merged = _Harness()._merge_metadata_batch_with_photo_tag_cache(
        {
            path: {
                "rating": 4,
                "tags": ["打架"],
            }
        }
    )

    assert merged[path]["rating"] == 4
    assert merged[path]["tags"] == ["捕食"]


def test_tag_cache_miss_loads_same_directory_asynchronously_and_emits_update(
    tmp_path: Path,
) -> None:
    app = QApplication.instance() or QApplication([])
    superpicky = tmp_path / ".superpicky"
    superpicky.mkdir()
    config_path = superpicky / "tags.cfg"
    config_path.write_text("打架\n捕食\n", encoding="utf-8")
    paths: list[str] = []
    for index in range(3):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(b"not an image")
        paths.append(os.path.normpath(str(path)))
    store = PhotoTagSidecarStore()
    assert store.set_tag_for_paths(
        [paths[0]],
        "打架",
        True,
        allowed_tags=["打架", "捕食"],
    ) == 1

    panel = SuperViewerTaggedFileListPanel(tag_config_path=config_path)
    panel._all_files = list(paths)
    updated: list[str] = []
    panel.metadata_cache_updated.connect(lambda values: updated.extend(list(values)))
    try:
        # The first lookup must return immediately rather than reading sidecars
        # synchronously on the selection path.
        assert panel.photo_tags_for_path(paths[0]) == set()
        deadline = time.monotonic() + 3.0
        while paths[0] not in panel._photo_tag_cache and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()

        assert panel.photo_tags_for_path(paths[0]) == {"打架"}
        assert paths[0] in updated
        assert len(panel._photo_tag_cache) == 3
    finally:
        panel.request_shutdown()
        deadline = time.monotonic() + 3.0
        while not panel.shutdown(wait_timeout_ms=25) and time.monotonic() < deadline:
            app.processEvents()
        assert panel.shutdown(wait_timeout_ms=25)
        panel.close()


def test_pending_tag_batch_starts_only_after_qthread_finished(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import SuperViewer.superviewer.tagged_file_list as tagged_module

    app = QApplication.instance() or QApplication([])
    config_path = tmp_path / "tags.cfg"
    config_path.write_text("打架\n", encoding="utf-8")
    first = os.path.normpath(str(tmp_path / "first.jpg"))
    second = os.path.normpath(str(tmp_path / "second.jpg"))
    Path(first).write_bytes(b"")
    Path(second).write_bytes(b"")

    class _ControlledWorker(tagged_module.PhotoTagCacheWorker):
        lock = threading.Lock()
        active = 0
        max_active = 0
        starts = 0

        def run(self) -> None:
            paths = list(self._paths)
            with type(self).lock:
                type(self).active += 1
                type(self).max_active = max(type(self).max_active, type(self).active)
                type(self).starts += 1
            try:
                self._finished_processed = len(paths)
                self._finished_total = len(paths)
                self.batch_ready.emit({path: set() for path in paths})
                self.finished_summary.emit(len(paths), len(paths))
                # Keep run() alive after the summary signal.  Starting pending
                # work from that signal would make max_active become two.
                time.sleep(0.05)
            finally:
                with type(self).lock:
                    type(self).active -= 1

    monkeypatch.setattr(tagged_module, "PhotoTagCacheWorker", _ControlledWorker)
    panel = SuperViewerTaggedFileListPanel(tag_config_path=config_path)
    try:
        panel._start_photo_tag_cache_loader_if_needed(
            [first],
            reason="test_first",
            allow_without_filters=True,
        )
        panel._start_photo_tag_cache_loader_if_needed(
            [second],
            reason="test_pending",
            allow_without_filters=True,
        )
        deadline = time.monotonic() + 3.0
        while (
            panel._photo_tag_loader is not None or panel._photo_tag_pending_paths
        ) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()

        assert _ControlledWorker.starts == 2
        assert _ControlledWorker.max_active == 1
        assert first in panel._photo_tag_cache
        assert second in panel._photo_tag_cache
    finally:
        panel.request_shutdown()
        deadline = time.monotonic() + 3.0
        while not panel.shutdown(wait_timeout_ms=25) and time.monotonic() < deadline:
            app.processEvents()
        assert panel.shutdown(wait_timeout_ms=25)
        panel.close()


def test_bounded_file_list_shutdown_does_not_wait_for_disk_writer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app_common.file_browser._panel as browser_panel_module

    app = QApplication.instance() or QApplication([])
    config_path = tmp_path / "tags.cfg"
    config_path.write_text("打架\n", encoding="utf-8")
    waits: list[bool] = []
    monkeypatch.setattr(
        browser_panel_module,
        "_shutdown_thumb_disk_writer",
        lambda wait=True: waits.append(bool(wait)),
    )

    panel = SuperViewerTaggedFileListPanel(tag_config_path=config_path)
    try:
        assert panel.shutdown(wait_timeout_ms=0)
        assert waits == [False]
    finally:
        panel.close()
        app.processEvents()
