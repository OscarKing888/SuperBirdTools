"""从导出清单构建新的成片工作区，不携带原图的选区、逐图裁切或分析缓存。"""
from copy import deepcopy

from birdstamp.workspace import WORKSPACE_FILE_EXTENSION, serialize_workspace_path


def unused_workspace_path(folder, stem):
    path = folder / f'{stem}{WORKSPACE_FILE_EXTENSION}'
    index = 2
    while path.exists():
        path = folder / f'{stem}_{index}{WORKSPACE_FILE_EXTENSION}'
        index += 1
    return path


def exported_workspace_payload(source_payload, paths, workspace_path, *, sequence_column):
    """保留模板/输出偏好，成片以完整画幅开始下一轮编辑。"""
    state = deepcopy(source_payload['editor_state'])
    settings = state['current_render_settings']
    for values in (settings, state['global_export_settings']):
        values.update(dejitter_strategy='none', dejitter_reference_enabled=False,
                      dejitter_reference_source=None, dejitter_reference_regions=[])
    settings.update(ratio='no_crop', center_mode='image', crop_box=None,
                    custom_center_x=None, custom_center_y=None,
                    crop_padding_top=0, crop_padding_bottom=0, crop_padding_left=0, crop_padding_right=0)
    state['dejitter_manual_matches'] = []
    state['dejitter_relay_anchors'] = []
    state['sequence_preview'].update(input_key=None, active=False, view='edit')
    state['preview'].update(edit_mode='none', preview_scale_percent=None)
    records = [serialize_workspace_path(path, workspace_path=workspace_path) for path in paths]
    return dict(report_databases=[],
                photos=[dict(path=record, sequence=index, render_settings=deepcopy(settings))
                        for index, record in enumerate(records, 1)],
                selection=dict(current_photo=records[0], selected_photos=[records[0]],
                               sort_column=sequence_column, sort_order='asc'),
                editor_state=state)
