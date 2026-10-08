"""命名安全区用户选项：持久化、真实对话框、运行时渲染和 CLI。"""
from copy import deepcopy
import json
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QMessageBox
import pytest

from test_platform_safe_area import _APP, payload, window, isolated_options
from birdstamp.overlays import safe_area_options as options
from birdstamp.overlays.safe_area import normalize_platform, safe_rect, layout_rect
from birdstamp.gui.user_options_dialog import UserOptionsDialog
from birdstamp.gui.editor_utils import path_key
from birdstamp.gui import editor_template as template
from birdstamp.export_stage import VideoFrameJob, source_frame_signature_for_job, build_default_image_proc_pipeline


def custom_options():
    values = options.default_options()
    values['labels']['my-group'] = '我的全屏图'
    values['presets']['my-group'] = dict(portrait=(0, .1, 0, .3), landscape=(.01, .05, .01, .15))
    return values


def select_group(dialog, key):
    for row in range(dialog.groups.count()):
        if dialog.groups.item(row).data(Qt.ItemDataRole.UserRole) == key:
            dialog.groups.setCurrentRow(row)
            return
    raise AssertionError(key)


def test_user_store_preserves_other_options_unicode_names_and_removed_groups():
    path = options.options_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'unrelated': {'value': '保留我'}}, ensure_ascii=False), encoding='utf-8')
    values = custom_options()
    del values['labels']['bilibili']; del values['presets']['bilibili']
    options.save_options(values)
    assert options.resolve_choice('我的全屏图') == 'my-group'
    assert normalize_platform('my-group') == 'my-group'
    assert normalize_platform('bilibili') == 'off'
    assert safe_rect((1000, 2000), 'my-group') == (0, 200, 1000, 1400)
    assert layout_rect((0, 200, 1000, 1400), (1000, 2000))[::2] == (0, 1000)
    assert safe_rect((2000, 1000), 'my-group') == (20, 50, 1980, 850)
    assert options.reload_options() == values
    assert '我的全屏图' in path.read_text(encoding='utf-8')
    assert json.loads(path.read_text(encoding='utf-8'))['unrelated'] == {'value': '保留我'}
    # 改显示名仍使用稳定组 ID，照片/工作区里的选择不失效。
    values['labels']['my-group'] = '改名后的组'
    options.save_options(values)
    assert normalize_platform('my-group') == 'my-group'
    assert options.resolve_choice('改名后的组') == 'my-group'
    assert options.resolve_choice('我的全屏图') is None


@pytest.mark.parametrize('bad', [(0, .8, 0, .3), (-.1, 0, 0, 0), (0, float('nan'), 0, 0), (1, 0, 0, 0)])
def test_invalid_margins_do_not_replace_last_saved_options(bad):
    options.save_options(custom_options())
    before = options.options_path().read_bytes()
    values = custom_options()
    values['presets']['my-group']['portrait'] = bad
    with pytest.raises(ValueError):
        options.save_options(values)
    assert options.options_path().read_bytes() == before
    assert options.current_options() == custom_options()


def test_failed_atomic_save_preserves_file_and_runtime(monkeypatch):
    options.save_options(custom_options())
    before = options.options_path().read_bytes()
    values = custom_options()
    values['labels']['my-group'] = '尚未保存'
    def fail(*args):
        raise OSError('模拟写入失败')
    monkeypatch.setattr(options.os, 'replace', fail)
    with pytest.raises(OSError):
        options.save_options(values)
    assert options.options_path().read_bytes() == before
    assert options.current_options() == custom_options()
    assert not list(options.options_path().parent.glob('*.tmp'))


def test_render_reads_cached_options_until_explicit_reload(monkeypatch):
    options.save_options(custom_options())
    def fail(*args, **kwargs):
        raise AssertionError('渲染不能逐帧读取磁盘')
    monkeypatch.setattr(Path, 'read_text', fail)
    for _ in range(4):
        assert safe_rect((1000, 2000), 'my-group') == (0, 200, 1000, 1400)
        assert normalize_platform('my-group') == 'my-group'


def test_dialog_cancel_validation_and_restore_defaults(tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args[-1]))
    dialog = UserOptionsDialog()
    try:
        assert dialog.groups.count() == 3
        select_group(dialog, 'xiaohongshu')
        for spins in dialog.margin_spins.values():
            assert spins[0].value() == spins[2].value() == 0
        dialog.margin_spins['portrait'][1].setValue(85)
        dialog.accept()
        assert errors and not options.options_path().exists()
        dialog.reset_groups()
        dialog.add_group()
        dialog.name_edit.setText('小红书')
        dialog.accept()
        assert len(errors) == 2 and not options.options_path().exists()
        dialog.name_edit.setText('竖屏作品')
        dialog.margin_spins['portrait'][3].setValue(35)
        dialog.copy_group()
        assert dialog.groups.count() == 5
        dialog.remove_group()
        assert dialog.groups.count() == 4
        dialog.show(); _APP.processEvents()
        assert dialog.grab().save(str(tmp_path/'safe-area-user-options.png'))
        dialog.reject()
        assert options.resolve_choice('竖屏作品') is None
        assert not options.options_path().exists()
    finally:
        dialog.close(); dialog.deleteLater(); _APP.processEvents()


def test_dialog_save_reload_and_empty_group_list():
    dialog = UserOptionsDialog()
    try:
        dialog.add_group()
        key = dialog.current_id
        dialog.name_edit.setText('我的专属安全区')
        dialog.margin_spins['portrait'][3].setValue(25)
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert options.reload_options()['presets'][key]['portrait'] == (0, 0, 0, .25)
    finally:
        dialog.deleteLater()
    reopened = UserOptionsDialog()
    try:
        select_group(reopened, key)
        assert reopened.name_edit.text() == '我的专属安全区'
        assert reopened.margin_spins['portrait'][3].value() == 25
        while reopened.groups.count():
            reopened.remove_group()
        reopened.accept()
        assert options.reload_options() == {'labels': {'off': '关闭（非全屏）'}, 'presets': {}}
    finally:
        reopened.deleteLater(); _APP.processEvents()


def test_editor_options_apply_immediately_preserve_choice_and_invalidate_cache(window, payload, tmp_path, monkeypatch):
    path = tmp_path/'bird.png'
    image = Image.new('RGB', (450, 800), '#203040')
    image.save(path)
    template_path = window.template_dir/'safe-test.json'
    template.save_template_payload(template_path, payload)
    window.template_paths['safe-test'] = template_path
    window.template_combo.addItem('safe-test')
    settings = window._build_current_render_settings()
    settings.update(template_name='safe-test', template_payload=payload, ratio='no_crop')
    window._append_photo_path_to_list(path, existing_keys=set(), default_settings=settings)
    window._store_preview_image_cache(window._preview_image_cache_signature(path), image)
    item = window._find_photo_item_by_path(path)
    window.photo_list.blockSignals(True)
    window.photo_list.setCurrentItem(item)
    window.photo_list.blockSignals(False)
    window._on_photo_selected(item, None)
    window.platform_safe_area_combo.setCurrentIndex(window.platform_safe_area_combo.findData('xiaohongshu'))
    old_key = window._original_mode_cache_key()
    job = VideoFrameJob(path, {'platform_safe_area': 'xiaohongshu'}, {}, {})
    old_signature = source_frame_signature_for_job(job)
    def accept_changed(dialog):
        select_group(dialog, 'xiaohongshu')
        dialog.name_edit.setText('小红书上下遮挡')
        dialog.margin_spins['portrait'][3].setValue(30)
        dialog.add_group()
        dialog.name_edit.setText('新增全屏组')
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(UserOptionsDialog, 'exec', accept_changed)
    window._open_user_options()
    window.render_preview()
    assert window.platform_safe_area_combo.findText('新增全屏组') >= 0
    assert window.platform_safe_area_combo.currentText() == '小红书上下遮挡'
    assert window.platform_safe_area_combo.currentData() == 'xiaohongshu'
    assert window.photo_render_overrides[path_key(path)]['platform_safe_area'] == 'xiaohongshu'
    assert window.preview_label.canvas._platform_safe_area_box == pytest.approx((0, .12, 1, .7))
    assert window._original_mode_cache_key() != old_key
    assert source_frame_signature_for_job(job) != old_signature
    assert window.safe_area_options_button.text() == '配置…'
    assert window.user_options_action.text() == '用户选项…'
    choices = next(d for d in build_default_image_proc_pipeline().ui_descriptors() if d.stage_id == 'template_crop').parameter_options
    choice = next(o for o in choices if o.key == 'platform_safe_area')
    assert any(c.label == '小红书上下遮挡' for c in choice.choices)


def test_custom_group_cli_uses_user_config(payload, tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from birdstamp import cli
    options.save_options(custom_options())
    source = tmp_path/'Bird.png'
    with Image.new('RGB', (450, 800)) as image:
        image.save(source)
    path = tmp_path/'template.json'
    template.save_template_payload(path, payload)
    monkeypatch.setattr(cli, 'extract_many_with_xmp_priority', lambda *a, **k: {source: {'SourceFile': str(source)}})
    result = CliRunner().invoke(cli.app, ['render', str(source), '--out', str(tmp_path/'output'),
        '--template', str(path), '--format', 'png', '--max-long-edge', '0', '--platform-safe-area', '我的全屏图'])
    assert result.exit_code == 0, result.output+str(result.exception)
    with Image.open(next((tmp_path/'output').glob('*.png'))) as image:
        assert image.getbbox()[3] <= 800*.7+1


def test_custom_group_workspace_restore_and_deleted_group_fallback(tmp_path):
    from birdstamp.workspace import read_workspace_json, write_workspace_json
    from birdstamp.gui.editor_renderer import _BirdStampRendererMixin
    values = custom_options()
    options.save_options(values)
    path = tmp_path/'命名安全区.birdstamp-workspace.json'
    write_workspace_json(path, {'photos': [{'render_settings': {'platform_safe_area': 'my-group'}}]})
    options.reload_options()
    saved = read_workspace_json(path)['photos'][0]['render_settings']
    assert _BirdStampRendererMixin()._normalize_render_settings(saved, {})['platform_safe_area'] == 'my-group'
    options.save_options(options.default_options())
    assert _BirdStampRendererMixin()._normalize_render_settings(saved, {})['platform_safe_area'] == 'off'


@pytest.mark.parametrize('raw', ['not a mapping', {'labels': {}, 'presets': []}])
def test_malformed_saved_options_fall_back_with_diagnostic(raw, caplog):
    path = options.options_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'platform_safe_area': raw}), encoding='utf-8')
    assert options.reload_options() == options.default_options()
    assert 'Cannot load safe area user options' in caplog.text
