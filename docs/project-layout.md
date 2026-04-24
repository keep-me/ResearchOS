# Project Layout

`ResearchOS` 现在采用的是“安全整理”方案：不直接照搬 `PaperMind` 去大规模搬目录，而是先把根目录职责、生成物边界和清理规则固定下来。

这么做的原因很现实：

- `apps/`、`packages/`、`frontend/`、`src-tauri/` 已经串起了 Python 后端、Web 前端、桌面端壳和 sidecar 装配。
- `dist/`、`data/`、`tmp/`、`src-tauri/target/` 这些目录里混有运行时数据和可再生构建物，不能简单一刀切。

目标不是把项目“看起来像 `PaperMind`”，而是把目录边界整理到和 `PaperMind` 一样清楚。

## 对标 PaperMind 的结构化视图

`PaperMind` 的根目录更克制，主要集中在：

- `apps/`
- `packages/`
- `frontend/`
- `docs/`
- `scripts/`
- `tests/`
- `data/`
- `infra/`
- `logs/`

`ResearchOS` 保留了这些核心层，同时多了桌面端和参考资料层：

- `src-tauri/`
  - Tauri 桌面端壳、打包入口、sidecar 装配。
- `dist/`
  - 已构建的 `researchos-server.exe`。
- `artifacts/`
  - UI smoke、运行时检查和诊断截图。
- `reference/`
  - 对照实现、兼容参考和迁移材料。
- `tmp/`、`build/`、`backups/`
  - 本地运行中间物、临时工作区、手工备份。

## 现在的根目录应该怎么理解

按职责可以分成 5 层：

- 产品源码
  - `apps/`
  - `packages/`
  - `frontend/`
  - `src-tauri/`
- 运行数据
  - `data/`
  - `logs/`
  - `projects/`
- 工程资产
  - `docs/`
  - `scripts/`
  - `tests/`
  - `infra/`
  - `design-system/`
  - `skills/`
- 参考资料
  - `reference/`
- 可再生生成物
  - `tmp/`
  - `build/`
  - `src-tauri/target/`
  - `src-tauri/binaries/`
  - `frontend/dist/`
  - `.pytest_cache/`
  - `.sandbox-home/`
  - `.sandbox-tmp/`

## 哪些目录应该长期保留

- `apps/`、`packages/`、`frontend/`、`src-tauri/`
  - 主代码，不要随意搬动。
- `data/`
  - 真实运行数据。这里要区分“业务数据”和“历史垃圾快照”。
- `dist/`
  - 当前桌面端打包链依赖 `researchos-server.exe`，默认保留。

## 哪些目录默认视为可清理

- `tmp/`
  - 本地 smoke、临时工作区、调试日志。
- `build/researchos-server/`
  - PyInstaller 中间目录。
- `src-tauri/target/`
  - Tauri 编译产物。
- `src-tauri/binaries/`
  - sidecar staging 目录，可从 `dist/` 重新生成。
- `frontend/dist/`
  - 前端编译产物。
- `.pytest_cache/`、`.ruff_cache/`、`.mypy_cache/`
  - 本地缓存。
- `data/snapshots/`
  - 仅清理已失效的临时测试快照，不碰当前真实工作区快照。

## 根目录收敛规则

后续继续整理时，统一遵循这些规则：

1. 新的调试脚本都放进 `scripts/`，不要继续堆在根目录。
2. 新的诊断文本、代码摘录、分析范围文件放进 `docs/project-tech/` 或 `artifacts/`。
3. 新的截图、录屏、smoke 输出统一沉到 `artifacts/`。
4. 备份压缩包优先放仓库外；如果必须留仓库内，只放 `backups/`，不要散落到根目录。
5. 不直接重排 `apps/ + packages/ + frontend/ + src-tauri/` 这 4 个主层，避免打断 import 和打包链。

## 清理脚本

仓库提供了安全版清理脚本：

```powershell
pwsh -NoLogo -File .\scripts\cleanup-workspace.ps1
pwsh -NoLogo -File .\scripts\cleanup-workspace.ps1 -Execute
pwsh -NoLogo -File .\scripts\cleanup-workspace.ps1 -Execute -PruneGit
```

脚本默认只审计，不删除。开启 `-Execute` 后会：

- 删除可再生的构建产物和本地缓存。
- 清理 `data/snapshots/` 里指向临时 pytest 工作区、且已经失效的快照仓库。
- 可选执行 `git gc --prune=now`。

`dist/researchos-server.exe` 默认不会删除；`backups/` 也只有显式传 `-RemoveBackups` 才会清理。

## 下一阶段如果继续向 PaperMind 靠拢

后续可以继续做 3 类低风险收敛：

1. 把根目录零散文档和范围摘录继续归并进 `docs/project-tech/`。
2. 把新的生成型输出限制在 `artifacts/`、`tmp/`、`data/` 这几个固定落点。
3. 继续把历史兼容层和测试残留整理到更清晰的模块边界，避免根目录再出现一次性集成代码。
