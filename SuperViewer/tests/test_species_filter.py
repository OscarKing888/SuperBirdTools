# -*- coding: utf-8 -*-
"""旧版鸟名附注只影响过滤键，不成为新的鸟种。"""
import pytest

from SuperViewer.superviewer.species_filter import species_filter_name


@pytest.mark.parametrize("value", [
    "反嘴鹬", " 反嘴鹬 ", "反嘴鹬（待确定 33%）", "反嘴鹬 (待确定 33.4%)",
    "反嘴鹬（待确定）", "反嘴鹬【待确认 33％】", "反嘴鹬[疑似]",
    "反嘴鹬 · 待确定", "反嘴鹬 待确认 33%", "反嘴鹬（未确认）",
    "反嘴鹬（已确认）", "反嘴鹬（置信度：99.5%）", "反嘴鹬 99%（已确定）",
    "反嘴鹬（疑似）（可信度 33%）",
])
def test_status_and_confidence_variants_share_primary_name(value):
    assert species_filter_name(value) == "反嘴鹬"


@pytest.mark.parametrize("value", [None, "", "  ", "待确定", "（待确认 33%）"])
def test_status_without_species_is_not_an_option(value):
    assert species_filter_name(value) == ""


@pytest.mark.parametrize("value", ["白鹭", "大白鹭", "白鹭（幼鸟）", "Black-crowned Night Heron"])
def test_distinct_species_and_other_name_content_remain_intact(value):
    assert species_filter_name(value) == value
