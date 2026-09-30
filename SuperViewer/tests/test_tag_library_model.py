import pytest

from SuperViewer.superviewer.tag_library_model import TagLibraryDraft
from SuperViewer.superviewer.photo_tags import parse_tag_tree_text, iter_tag_tree_leaves


def test_comments_chinese_crlf_and_noop_roundtrip():
    raw = '# 鸟类\r\n行为\r\n\t飞行\r\n\t捕食\r\n# 尾注\r\n'.encode('utf-8')
    draft = TagLibraryDraft(raw)
    assert draft.serialize() == raw
    draft.rename(draft.roots[0].children[0], '盘旋')
    encoded = draft.serialize()
    assert '# 鸟类\r\n' in encoded.decode('utf-8')
    assert '# 尾注\r\n' in encoded.decode('utf-8')
    assert b'\n' not in encoded.replace(b'\r\n', b'')
    assert iter_tag_tree_leaves(parse_tag_tree_text(encoded.decode('utf-8'))) == ['盘旋', '捕食']


def test_duplicate_leaf_identity_rename_all_and_last_removal():
    draft = TagLibraryDraft('甲\n  飞行\n乙\n  飞行\n'.encode())
    first = draft.roots[0].children[0]
    second = draft.roots[1].children[0]
    draft.rename(first, '盘旋')
    draft.rename(second, '滑翔')
    assert first.name == second.name == '滑翔'
    assert draft.migration() == {'飞行': '滑翔'}
    draft.remove(first)
    assert draft.migration() == {'飞行': '滑翔'}
    draft.remove(second)
    assert draft.migration() == {'飞行': None}
    with pytest.raises(ValueError, match='为空'):
        draft.serialize()


def test_move_reorder_groups_do_not_migrate_photos():
    draft = TagLibraryDraft('甲\n  飞行\n  捕食\n乙\n  湿地\n'.encode())
    group, other = draft.roots
    bird = group.children[0]
    draft.move(bird, other)
    draft.reorder(other, -1)
    draft.rename(group, '行为')
    assert draft.migration() == {}
    assert draft.roots[0].children[-1] is bird
    assert draft.serialize()
    with pytest.raises(ValueError, match='自身'):
        draft.move(other, other)


def test_delete_then_add_same_name_is_not_rename_or_noop():
    draft = TagLibraryDraft('飞行\n'.encode())
    draft.remove(draft.roots[0])
    draft.add('飞行')
    assert draft.migration() == {'飞行': None}
    assert draft.changed


@pytest.mark.parametrize('name', ['', '  ', '# 注释', '甲\n乙', '甲\t乙', '甲\x00乙'])
def test_invalid_names(name):
    draft = TagLibraryDraft(None)
    with pytest.raises(ValueError):
        draft.add(name)


def test_conflicts_and_empty_groups():
    draft = TagLibraryDraft('飞行\n捕食\n'.encode())
    with pytest.raises(ValueError):
        draft.rename(draft.roots[0], '捕食')
    group = draft.add('行为', group=True)
    with pytest.raises(ValueError, match='为空'):
        draft.serialize()
    draft.move(draft.roots[0], group)
    assert draft.serialize()
    with pytest.raises(ValueError):
        draft.add('飞行')


def test_deleted_group_keeps_comments_and_deletes_only_last_occurrence():
    draft = TagLibraryDraft('甲\n  # 保留注释\n  飞行\n  捕食\n乙\n  飞行\n'.encode())
    draft.remove(draft.roots[0])
    assert draft.migration() == {'捕食': None}
    assert '# 保留注释' in draft.serialize().decode()
