from SuperViewer.superviewer.image_info_tab_image_info import _metadata_comment


def test_metadata_comment_accepts_parsed_file_list_comment_field() -> None:
    assert _metadata_comment({"comment": "sidecar note"}) == "sidecar note"
