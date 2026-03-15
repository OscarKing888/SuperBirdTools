import sqlite3
from pathlib import Path

from app_common.report_db import (
    ReportDB,
    existing_report_db_paths,
    find_superpicky_report_db_paths,
    find_report_root,
    report_row_to_exiftool_style,
    resolve_existing_report_db_path,
)


def _touch_sqlite_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS smoke_test (id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()


def test_existing_report_db_paths_prioritizes_superpicky(tmp_path: Path) -> None:
    root_db = tmp_path / "report.db"
    superpicky_db = tmp_path / ".superpicky" / "report.db"
    _touch_sqlite_db(root_db)
    _touch_sqlite_db(superpicky_db)

    paths = existing_report_db_paths(str(tmp_path))

    assert paths == [str(superpicky_db), str(root_db)]
    assert resolve_existing_report_db_path(str(tmp_path)) == str(superpicky_db)


def test_open_if_exists_supports_root_report_db_and_find_report_root(tmp_path: Path) -> None:
    root_db = tmp_path / "report.db"
    _touch_sqlite_db(root_db)
    nested = tmp_path / "child" / "leaf"
    nested.mkdir(parents=True)

    db = ReportDB.open_if_exists(str(tmp_path))
    assert db is not None
    try:
        assert Path(db.db_path) == root_db
        assert db.exists() is True
    finally:
        db.close()

    assert find_report_root(str(nested), max_levels=4) == str(tmp_path)


def test_find_superpicky_report_db_paths_walks_up_and_respects_max_levels(tmp_path: Path) -> None:
    level1 = tmp_path / "level1"
    level2 = level1 / "level2"
    level3 = level2 / "level3"
    level4 = level3 / "level4"
    level4.mkdir(parents=True)

    current_db = level4 / ".superpicky" / "report.db"
    parent_db = level3 / ".superpicky" / "report.db"
    ancestor_db = level1 / ".superpicky" / "report.db"
    out_of_limit_db = tmp_path / ".superpicky" / "report.db"
    plain_db = level2 / "report.db"

    _touch_sqlite_db(current_db)
    _touch_sqlite_db(parent_db)
    _touch_sqlite_db(ancestor_db)
    _touch_sqlite_db(out_of_limit_db)
    _touch_sqlite_db(plain_db)

    paths = find_superpicky_report_db_paths(str(level4), max_levels=3)

    assert paths == [str(current_db), str(parent_db), str(ancestor_db)]


def test_report_db_schema_contains_pick_column(tmp_path: Path) -> None:
    db = ReportDB(str(tmp_path), create_if_missing=True)
    try:
        cols = [row[1] for row in db._conn.execute("PRAGMA table_info(photos)").fetchall()]
    finally:
        db.close()

    assert "pick" in cols


def test_report_row_to_exiftool_style_maps_pick_flag() -> None:
    rec = report_row_to_exiftool_style(
        {
            "filename": "sample",
            "rating": 4,
            "pick": 1,
            "bird_species_cn": "黑脸琵鹭",
        },
        "/tmp/sample.jpg",
    )

    assert rec["XMP-dc:Title"] == "黑脸琵鹭"
    assert rec["XMP-xmp:Rating"] == 4
    assert rec["XMP-xmpDM:pick"] == 1
