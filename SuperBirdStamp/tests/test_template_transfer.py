"""模板 JSON 交换：中文、内嵌素材、同名保护及真实编辑器待输入文字。"""
import pytest
from PIL import Image
from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from birdstamp import config, workspace
from birdstamp.gui import editor_template as templates
from birdstamp.gui.editor_template_dialog import TemplateManagerDialog
from birdstamp.overlays.assets import decode_asset, import_image
from birdstamp.overlays.model import new_item


_APP = QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "get_user_data_dir", lambda: tmp_path / "user")
    monkeypatch.setattr(templates, "_copy_missing_seed_templates", lambda folder: None)


def test_legacy_import_keeps_chinese_and_crop_settings(tmp_path):
    source = tmp_path / "中文模板.json"
    templates.save_template_payload(source, dict(
        name="旧模板", ratio=1.5, center_mode="custom", custom_center_x=0.3,
        crop_padding_left=25, banner_color="#123456",
        fields=[dict(name="中文鸟名", tag="bird", color="#123456")],
    ))
    expected = templates.load_template_payload(source)
    # Windows 编辑器保存的 UTF-8 BOM 也能导入。
    source.write_text(source.read_text(encoding="utf-8"), encoding="utf-8-sig")
    imported = templates.import_template_file(source, tmp_path / "templates")
    expected["name"] = "中文模板"
    assert templates.load_template_payload(imported) == expected
    assert "中文鸟名" in imported.read_text(encoding="utf-8")


def test_overlay_export_import_preserves_embedded_image(tmp_path):
    image_path = tmp_path / "水印.png"
    with Image.new("RGBA", (16, 12), (30, 90, 150, 128)) as image:
        image.save(image_path)
    key, asset = import_image(image_path)
    payload = dict(name="含图模板", ratio="no_crop", overlays=[
        dict(new_item("text"), text="翠鸟与荷花"),
        dict(new_item("image"), asset_id=key),
    ], overlay_assets={key: asset})
    source = tmp_path / "分享.json"
    templates.save_template_payload(source, payload)
    image_path.unlink()
    imported = templates.import_template_file(source, tmp_path / "templates")
    result = templates.load_template_payload(imported)
    expected = templates.load_template_payload(source)
    expected["name"] = "分享"
    assert result == expected
    with decode_asset(key, result["overlay_assets"]) as decoded:
        assert decoded.size == (16, 12)
        assert decoded.getpixel((0, 0)) == (30, 90, 150, 128)


def test_import_same_file_and_case_collisions_never_replace(tmp_path):
    folder = tmp_path / "templates"
    folder.mkdir()
    source = folder / "Bird.json"
    templates.save_template_payload(source, dict(name="Bird", fields=[]))
    before = source.read_bytes()
    (folder / "BIRD_2.JSON").write_text("keep", encoding="utf-8")
    imported = templates.import_template_file(source, folder)
    assert imported.name == "Bird_3.json"
    assert templates.load_template_payload(imported)["name"] == "Bird_3"
    assert source.read_bytes() == before
    assert (folder / "BIRD_2.JSON").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("text", [
    "{", "[]", "{}", '{"name":"其他配置"}', '{"fields":{}}',
    '{"fields":[1]}', '{"overlays":{}}', '{"overlays":[null]}',
    '{"overlays":[],"overlay_version":999}',
    '{"overlays":[],"overlay_assets":[]}',
])
def test_invalid_import_creates_no_template(tmp_path, text):
    source = tmp_path / "无效.json"
    source.write_text(text, encoding="utf-8")
    folder = tmp_path / "templates"
    with pytest.raises(ValueError):
        templates.import_template_file(source, folder)
    assert not folder.exists()


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    monkeypatch.setattr(TemplateManagerDialog, "_load_preview_source", lambda self: None)
    monkeypatch.setattr(TemplateManagerDialog, "_preview_source_bird_box", lambda self: None)
    folder = tmp_path / "templates"
    templates.save_template_payload(folder / "当前模板.json", dict(
        name="当前模板", ratio="no_crop", overlays=[new_item("text")]))
    with Image.new("RGB", (320, 200), "#405060") as image:
        widget = TemplateManagerDialog(folder, image)
    widget.overlay_panel.select(widget.overlay_panel.doc["overlays"][0]["id"])
    try:
        yield widget
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _APP.processEvents()


def select_export(monkeypatch, destination):
    def accept(dialog):
        assert dialog.acceptMode() == QFileDialog.AcceptMode.AcceptSave
        assert dialog.defaultSuffix() == "json"
        assert not dialog.testOption(QFileDialog.Option.DontConfirmOverwrite)
        dialog.selectFile(str(destination))
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QFileDialog, "exec", accept)


def test_export_button_flushes_pending_text_and_appends_json(dialog, tmp_path, monkeypatch):
    select_export(monkeypatch, tmp_path / "分享")
    dialog.overlay_panel.text.setPlainText("刚刚输入的中文")
    assert dialog.overlay_panel._text_timer.isActive()
    dialog.show()
    dialog.export_template_btn.setFocus()
    QTest.keyClick(dialog.export_template_btn, Qt.Key.Key_Space)
    exported = templates.load_template_payload(tmp_path / "分享.json")
    assert exported["overlays"][0]["text"] == "刚刚输入的中文"
    assert dialog.current_template_name == "当前模板"
    assert dialog.template_list.count() == 1


def test_import_button_selects_new_template_and_preserves_pending_edit(dialog, tmp_path, monkeypatch):
    source = tmp_path / "当前模板.json"
    templates.save_template_payload(source, dict(name="内部名称", fields=[]))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    dialog.overlay_panel.text.setPlainText("导入前的编辑")
    dialog.import_template_btn.click()
    assert dialog.current_template_name == "当前模板_2"
    assert dialog.template_list.currentItem().text() == "当前模板_2"
    assert dialog.export_template_btn.isEnabled()
    original = templates.load_template_payload(dialog.template_dir / "当前模板.json")
    assert original["overlays"][0]["text"] == "导入前的编辑"


def test_cancel_and_invalid_import_leave_selection_and_files(dialog, tmp_path, monkeypatch):
    before = {p.name: p.read_bytes() for p in dialog.template_dir.iterdir()}
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    dialog.import_template_btn.click()
    dialog.export_template_btn.click()
    source = tmp_path / "错误.json"
    source.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    dialog.import_template_btn.click()
    assert warnings and warnings[0][0] == "导入模板失败"
    assert dialog.current_template_name == "当前模板"
    assert {p.name: p.read_bytes() for p in dialog.template_dir.iterdir()} == before


def test_failed_export_preserves_existing_destination(dialog, tmp_path, monkeypatch):
    destination = tmp_path / "已有模板.json"
    destination.write_text('{"fields":[]}', encoding="utf-8")
    before = destination.read_bytes()
    select_export(monkeypatch, destination)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[1:]))
    def fail_replace(*args):
        raise OSError("目标文件不可写")
    monkeypatch.setattr(workspace.os, "replace", fail_replace)
    dialog.export_template_btn.click()
    assert destination.read_bytes() == before
    assert warnings and warnings[0][0] == "导出模板失败"
    assert "目标文件不可写" in warnings[0][1]
    assert not list(tmp_path.glob(".*.tmp"))
