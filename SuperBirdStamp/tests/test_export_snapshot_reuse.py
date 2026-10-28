"""JPEG-to-PNG reuse keeps the metadata version captured for its old pixels."""
import threading

import pytest
from PIL import Image

from app_common.exif_io import get_exiftool_executable_path, metadata_store
from app_common.exif_io.photo_meta import PhotoMetaDataXMP
from birdstamp import export_metadata, export_stage
from birdstamp.export_frame_cache import create_frame_cache_plan
from birdstamp.export_snapshot import prepare_export_job
from birdstamp.export_stage import core
from birdstamp.gui import editor_exporter


@pytest.mark.parametrize("pipeline", ["image", "retained_frame"])
def test_jpeg_cache_reuse_preserves_frozen_xmp_after_another_edit(tmp_path, monkeypatch, pipeline):
    if not get_exiftool_executable_path():
        pytest.skip("ExifTool unavailable")
    store = metadata_store.MetadataStore(tmp_path / "store.sqlite3", start_scheduler=False)
    monkeypatch.setattr(metadata_store, "_default", store)
    original = (tmp_path / "中文原图.jpg").resolve()
    exported = tmp_path / "旧成片.jpg"
    try:
        with Image.new("RGB", (24, 16), "blue") as image:
            image.save(original)
            store.submit_edit(str(original), {"title": "旧苍鹭"}).result()
            job = export_stage.VideoFrameJob(original, {}, {}, {})
            prepare_export_job(job)
            signature = export_stage.source_frame_signature_for_job(job)
            export_metadata.save_export_image(
                image, exported, source_path=original, format="JPEG", xmp_snapshot=job.export_metadata.xmp,
            )
        store.submit_edit(str(original), {"title": "新白鹭"}).result()
        assert store.flush_paths([str(original)])
        assert PhotoMetaDataXMP().read(str(original))["XMP-dc:Title"] == "新白鹭"
        monkeypatch.setattr(export_metadata, "read_export_xmp",
                            lambda *_: pytest.fail("JPEG reuse read live source XMP"))
        if pipeline == "image":
            monkeypatch.setattr(editor_exporter, "render_video_frame",
                                lambda *a, **k: pytest.fail("valid JPEG reuse re-rendered source"))
            output = tmp_path / "reused.png"
            editor_exporter._BirdStampExporterMixin()._render_and_save_image_task(
                editor_exporter._ImageExportTask(job, output, fast_png=True, reuse_source=exported),
                template_paths={}, bird_box_cache={}, bird_box_lock=threading.Lock(),
            )
        else:
            monkeypatch.setattr(export_stage, "render_video_frame",
                                lambda *a, **k: pytest.fail("valid JPEG reuse re-rendered source"))
            plan = create_frame_cache_plan(tmp_path / "movie.mp4", bucket_kind="source",
                                           bucket_key="reuse", persistent=True)
            result = core._render_and_cache_source_frame(
                job=job, index=1, source_plan=plan, template_paths={}, frame_signature=signature,
                bird_box_cache={}, bird_box_lock=threading.Lock(), cancel_event=None, exported_image=exported,
            )
            output = result[2]
            assert result[4] == signature
            assert result[5] == exported
        assert job.raw_metadata["Title"] == "旧苍鹭"
        assert PhotoMetaDataXMP().read(str(output))["XMP-dc:Title"] == "旧苍鹭"
        assert export_stage.source_frame_signature_for_job(job) == signature
        with Image.open(exported) as old, Image.open(output) as copied:
            assert copied.tobytes() == old.tobytes()
    finally:
        store.close()
