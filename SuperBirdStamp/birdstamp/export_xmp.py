"""成片 XMP 合并：侧车优先，保留未知命名空间及完整 RDF 属性。"""
from __future__ import annotations

from pathlib import Path
from xml.dom import Node, minidom
from xml.etree import ElementTree as ET
from xml.parsers.expat import ExpatError

from app_common.exif_io.xmp_sidecar import _photo_descriptions

_RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
_XMLNS = 'http://www.w3.org/2000/xmlns/'
_XML = 'http://www.w3.org/XML/1998/namespace'
_EMPTY = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/">'
          b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>'
          b'</x:xmpmeta>')


def _descriptions(document):
    rdfs = document.getElementsByTagNameNS(_RDF, 'RDF')
    if len(rdfs) != 1:
        raise ValueError('XMP 必须包含一个 RDF 数据集。')
    rdf = rdfs[0]
    return rdf, [node for node in rdf.childNodes
                 if node.nodeType == Node.ELEMENT_NODE
                 and node.namespaceURI == _RDF and node.localName == 'Description']


def _photo_indices(payload: bytes, source: Path) -> set[int]:
    # 与共享 XMP 读取器使用同一照片资源识别规则（路径、空 about、UUID）。
    root = ET.fromstring(payload)
    rdf = root if root.tag == f'{{{_RDF}}}RDF' else root.find(f'.//{{{_RDF}}}RDF')
    if rdf is None:
        raise ValueError('XMP 缺少 RDF 数据集。')
    selected = _photo_descriptions(root, str(source))
    return {index for index, node in enumerate(rdf.findall(f'{{{_RDF}}}Description'))
            if node in selected}


def _properties(description):
    for attribute in description.attributes.values():
        if attribute.namespaceURI not in {_RDF, _XMLNS, _XML, None}:
            yield attribute
    for child in description.childNodes:
        if child.nodeType == Node.ELEMENT_NODE:
            yield child


def _import_description(document, description):
    result = document.importNode(description, deep=True)
    # 属性值可能引用命名空间前缀；DOM 保留原前缀，并补齐祖先上的声明。
    inherited = {}
    node = description
    while node.nodeType == Node.ELEMENT_NODE:
        for attribute in node.attributes.values():
            if attribute.namespaceURI in {_XMLNS, _XML}:
                inherited.setdefault(attribute.name, attribute)
        node = node.parentNode
    for name, attribute in inherited.items():
        result.setAttributeNS(attribute.namespaceURI, name, attribute.value)
    return result


def _bind_to_export(description):
    about = description.getAttributeNodeNS(_RDF, 'about')
    if about is not None:
        # 保持原前缀；minidom.setAttributeNS 改已有属性前缀会破坏其内部索引。
        about.value = ''
    else:
        description.setAttributeNS(_XMLNS, 'xmlns:rdf', _RDF)
        description.setAttributeNS(_RDF, 'rdf:about', '')


def merge_export_xmp(embedded: bytes, sidecar: bytes, *, source: Path) -> bytes:
    """整属性覆盖（包括数组/多语言/空值），不经 ExifTool 已知标签白名单。"""
    embedded = embedded.strip() or _EMPTY
    try:
        with minidom.parseString(embedded) as base, minidom.parseString(sidecar) as edits:
            base_rdf, base_descriptions = _descriptions(base)
            _, edit_descriptions = _descriptions(edits)
            base_photos = [base_descriptions[i] for i in sorted(_photo_indices(embedded, source))]
            edit_indices = _photo_indices(sidecar, source)
            keys = {(prop.namespaceURI, prop.localName)
                    for index in edit_indices for prop in _properties(edit_descriptions[index])}
            for description in base_photos:
                for prop in list(_properties(description)):
                    if (prop.namespaceURI, prop.localName) in keys:
                        if prop.nodeType == Node.ATTRIBUTE_NODE:
                            description.removeAttributeNode(prop)
                        else:
                            description.removeChild(prop)
                _bind_to_export(description)
            for index, description in enumerate(edit_descriptions):
                copied = _import_description(base, description)
                if index in edit_indices:
                    _bind_to_export(copied)
                base_rdf.appendChild(copied)
            # ExifTool 块写入要求 xpacket 或 x:xmpmeta 起始，不能带 XML 声明。
            return (b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
                    + base.documentElement.toxml(encoding='utf-8')
                    + b'\n<?xpacket end="w"?>')
    except (ET.ParseError, ValueError, ExpatError) as exc:
        raise ValueError(f'无法合并原图 XMP：{source}：{exc}') from exc
