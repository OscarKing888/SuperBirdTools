"""Editable tags.cfg document. Node identity survives renames and moves."""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from .photo_tags import parse_tag_tree_text


@dataclass(eq=False)
class EditableTag:
    name: str
    group: bool = False
    children: list[EditableTag] = field(default_factory=list)
    identity: str = field(default_factory=lambda: uuid4().hex)
    origin: str | None = None
    comments: list[str] = field(default_factory=list)


def validate_tag_name(name: str) -> str:
    if any(char in name for char in "\r\n\t\x00"):
        raise ValueError("标签名称不能包含换行、制表符或空字符。")
    name = name.strip()
    if not name or name.startswith("#"):
        raise ValueError("请输入非空名称，且不能以 # 开头。")
    return name


class TagLibraryDraft:
    def __init__(self, raw: bytes | None) -> None:
        self.original = raw
        text = (raw or b"").decode("utf-8-sig")
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.bom = b"\xef\xbb\xbf" if (raw or b"").startswith(b"\xef\xbb\xbf") else b""
        self.roots: list[EditableTag] = []
        self.trailing: list[str] = []
        stack: list[tuple[int, EditableTag]] = []
        comments: list[str] = []
        for line in text.splitlines():
            expanded = line.expandtabs(4)
            name = expanded.strip()
            if not name or name.startswith("#"):
                comments.append(line)
                continue
            indent = len(expanded) - len(expanded.lstrip(" "))
            while stack and indent <= stack[-1][0]:
                stack.pop()
            siblings = stack[-1][1].children if stack else self.roots
            # Match the existing parser's first-sibling-wins semantics.
            if any(node.name == name for node in siblings):
                continue
            node = EditableTag(name, comments=comments)
            comments = []
            siblings.append(node)
            if stack:
                stack[-1][1].group = True
            stack.append((indent, node))
        self.trailing = comments
        for node in self.nodes():
            if not node.group:
                node.origin = node.name
        self.original_tags = {node.name for node in self.nodes() if not node.group}
        self.original_groups = {node.identity: node.name for node in self.nodes() if node.group}
        self._initial = self._state()

    def nodes(self):
        def walk(items):
            for node in items:
                yield node
                yield from walk(node.children)
        return list(walk(self.roots))

    def _state(self):
        def state(nodes):
            return tuple((n.identity, n.name, n.group, state(n.children)) for n in nodes)
        return state(self.roots)

    @property
    def changed(self) -> bool:
        return self._state() != self._initial

    def siblings(self, node: EditableTag) -> list[EditableTag]:
        for items in [self.roots, *(n.children for n in self.nodes())]:
            if node in items:
                return items
        raise ValueError("标签已被删除。")

    def add(self, name: str, *, group=False, parent: EditableTag | None = None) -> EditableTag:
        name = validate_tag_name(name)
        siblings = self._destination(parent)
        if any(n.name == name for n in siblings) or (
            not group and any(n.name == name and not n.group for n in self.nodes())
        ):
            raise ValueError("该名称已存在。")
        node = EditableTag(name, group=group)
        siblings.append(node)
        return node

    def rename(self, node: EditableTag, name: str) -> None:
        name = validate_tag_name(name)
        if name == node.name:
            return
        targets = [node] if node.group else [n for n in self.nodes() if not n.group and n.name == node.name]
        if not node.group and any(not n.group and n.name == name for n in self.nodes()):
            raise ValueError("目标标签已存在，不能自动合并。")
        for target in targets:
            if any(n not in targets and n.name == name for n in self.siblings(target)):
                raise ValueError("同一分组中已存在该名称。")
        for target in targets:
            target.name = name

    def remove(self, node: EditableTag) -> None:
        def comments(n):
            return n.comments + [line for child in n.children for line in comments(child)]
        self.trailing.extend(comments(node))
        self.siblings(node).remove(node)

    def _destination(self, parent):
        if parent is not None and (parent not in self.nodes() or not parent.group):
            raise ValueError("所属位置必须是分组或顶层。")
        return self.roots if parent is None else parent.children

    def move(self, node: EditableTag, parent: EditableTag | None, index: int | None = None):
        def contains(root, target):
            return root is target or any(contains(child, target) for child in root.children)
        if parent is not None and contains(node, parent):
            raise ValueError("不能将分组移动到自身或其子分组。")
        destination = self._destination(parent)
        if any(n is not node and n.name == node.name for n in destination):
            raise ValueError("目标位置已存在同名项。")
        self.siblings(node).remove(node)
        destination.insert(len(destination) if index is None else max(0, index), node)

    def reorder(self, node, offset):
        siblings = self.siblings(node)
        old = siblings.index(node)
        new = max(0, min(len(siblings) - 1, old + offset))
        siblings.insert(new, siblings.pop(old))

    def migration(self) -> dict[str, str | None]:
        result = {}
        for old in sorted(self.original_tags):
            remaining = [n for n in self.nodes() if not n.group and n.origin == old]
            new = remaining[0].name if remaining else None
            if new != old:
                result[old] = new
        return result

    def summary(self) -> str:
        lines = []
        groups = {node.identity: node.name for node in self.nodes() if node.group}
        for identity, name in self.original_groups.items():
            if identity not in groups:
                lines.append(f"删除分组及其子树：{name}")
            elif groups[identity] != name:
                lines.append(f"重命名分组：{name} → {groups[identity]}")
        lines.extend(f"新增分组：{name}" for identity, name in groups.items() if identity not in self.original_groups)
        for old, new in self.migration().items():
            lines.append(f"重命名：{old} → {new}" if new else f"删除：{old}")
        lines.extend(f"新增：{n.name}" for n in self.nodes() if not n.group and n.origin is None)
        return "\n".join(lines) or "调整标签分组或顺序"

    def serialize(self) -> bytes | None:
        if not self.changed:
            return self.original
        lines = []

        def walk(nodes, depth=0):
            for node in nodes:
                validate_tag_name(node.name)
                if node.group and not node.children:
                    raise ValueError(f"分组「{node.name}」为空，请添加子项或删除该分组。")
                lines.extend(node.comments)
                # A common indentation avoids malformed hierarchy after moves.
                lines.append("    " * depth + node.name)
                walk(node.children, depth + 1)
        walk(self.roots)
        lines.extend(self.trailing)
        text = self.newline.join(lines) + (self.newline if lines else "")
        # Round-trip validation guards against accidentally serializing a group as a leaf.
        parsed = parse_tag_tree_text(text)
        if sum(1 for _ in self.nodes()) != self._count(parsed):
            raise ValueError("标签结构包含重复或无法保存的节点。")
        return self.bom + text.encode("utf-8")

    @staticmethod
    def _count(nodes):
        return sum(1 + TagLibraryDraft._count(n.children) for n in nodes)
