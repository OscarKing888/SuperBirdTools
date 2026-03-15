---
name: refactor-python-class-per-file
description: >-
  Refactors Python modules by moving each class into its own file without
  changing behavior; verifies no regressions (tests, py_compile), fixes bugs
  discovered during refactoring, and adds or updates documentation. Use when
  the user asks to refactor Python code by splitting classes into separate
  files, one-class-per-file layout, or to reorganize a module without changing
  logic.
---

# Python 一类一文件重构

将 Python 模块按「每个类一个文件」拆分，不修改逻辑，避免回归，顺带修 bug 并补充文档。

## 工作流程

按以下顺序执行，每步完成后再进入下一步。

### 1. 分析目标模块

- 列出模块内所有**类**（含嵌套类是否要拆出）。
- 理清依赖：每个类的基类、所引用的其他类/函数、被谁引用。
- 确认包结构：新文件放在同包下还是新建子包；与现有 `__init__.py` 的兼容方式。

### 2. 制定拆分方案

- **文件命名**：一个类一个文件，文件名用 snake_case，通常与类名对应（如 `MyService` → `my_service.py`）。
- **放置位置**：与当前模块同目录，或放入合理子包；保持 import 路径简短、一致。
- **兼容旧导入**：在包 `__init__.py` 或原入口模块中 re-export 被拆出的类，使 `from package import OldClass` 等现有用法仍可用，除非明确要求改为新路径。

### 3. 实施拆分

- **单类单文件**：把类完整搬入新文件，不改变方法体、属性、继承关系。
- **导入**：在新文件中补全所需 import（标准库、同包/父包、第三方）；优先使用相对导入（`from .foo import Bar`）以保持包内引用清晰。
- **原文件**：删除已迁出的类；若原文件仅剩 re-export，保留为薄包装层并注明「仅为兼容保留」。

### 4. 保持行为一致、避免回归

- **不改逻辑**：仅做移动与 import 调整，不重写算法、不改变公开 API 行为。
- **验证**：
  - 对改动过的包运行：`py -3 -m py_compile <模块或包路径>`（或 `python3 -m py_compile`）。
  - 若有测试：运行完整测试套件；若有 lint/type-check：一并跑过。
- **回归处理**：若测试或编译失败，先修复再继续；若发现明显 bug（如错误用法、遗漏边界情况），在本次重构中一并修正并记录。

### 5. 顺带修 Bug

- 重构过程中若发现：
  - 错误用法（错误类型、错误参数）
  - 明显遗漏（未处理 None、空集合、异常路径）
  - 与注释/文档不一致的行为  
  则在本轮修改中修正，并在文档/提交说明中写明「重构时顺带修复」。

### 6. 文档说明

- **必做**：在包或模块层级补充/更新说明，包含：
  - 新结构概述：哪些类在哪些文件，主要职责。
  - 对外入口：推荐从何处 import（如 `from package import X, Y`）。
  - 若保留了兼容 re-export，注明「为兼容保留，新代码建议从新路径导入」。
- **可选**：在关键类或模块的 docstring 中简要说明职责与用法；若项目有 CHANGELOG/README，增加「重构：一类一文件 + 顺带修复」的简短条目。

## 检查清单

执行前可勾选确认：

```
- [ ] 已列出目标模块中所有类及依赖关系
- [ ] 已确定每个类对应的新文件名与所在包/目录
- [ ] 已规划 __init__.py 或入口的 re-export，保证现有 import 不报错
- [ ] 已按「一类一文件」完成迁移，未改动类内部逻辑
- [ ] 已运行 py_compile 且通过
- [ ] 已运行测试套件（如有）且通过
- [ ] 发现的 bug 已修复并在文档/提交中说明
- [ ] 已更新包/模块文档（结构说明 + 入口说明 + 兼容说明）
```

## 注意事项

- **嵌套类**：若嵌套类只被外层类使用，可保留在同一文件；若被多处引用或体量较大，可拆成独立文件并在原处用 import 引用。
- **循环依赖**：若拆开后出现包内循环 import，应通过提取公共依赖到单独模块或调整层次消除，而不是在重构中引入新逻辑。
- **跨平台**：路径与 shebang 保持与项目约定一致（如 AGENTS.md/CLAUDE 中关于 Windows + macOS 的约定）。

## 简要示例

**重构前**（`services/handlers.py`）：

```python
class RequestParser:
    ...

class ResponseBuilder:
    ...

class Handler:
    def __init__(self):
        self.parser = RequestParser()
        self.builder = ResponseBuilder()
```

**重构后**：

- `services/request_parser.py`：仅含 `RequestParser`，所需 import 写在文件顶。
- `services/response_builder.py`：仅含 `ResponseBuilder`。
- `services/handler.py`：仅含 `Handler`，`from .request_parser import RequestParser` 等。
- `services/__init__.py`：`from .request_parser import RequestParser`；`from .response_builder import ResponseBuilder`；`from .handler import Handler`；必要时 `__all__ = [...]`，这样 `from services import Handler` 仍可用。

文档中说明：`Handler`、`RequestParser`、`ResponseBuilder` 分别位于 `handler`、`request_parser`、`response_builder` 模块，推荐从 `services` 包顶层导入以保持兼容。
