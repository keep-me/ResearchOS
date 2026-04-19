# MinerU Runtime

这个目录只用于本地 MinerU 运行时资源，不存放源码。

目录说明：

- `models/`: 可选的本地模型目录，优先使用 `MinerU/models/pipeline/`
- `runtime/`: 论文 OCR 结果缓存
- `cache/`: 本地运行时缓存
- `mineru.local.json`: 运行时自动生成的本地配置

当前项目使用的是 `pip` 安装后的 `mineru` 包，并通过 `packages/ai/mineru_runtime.py` 调用本地 `pipeline` 后端。

如果 `MinerU/models/pipeline/` 下没有模型，运行时会优先复用当前系统已有的 `~/mineru.json` 中配置的 pipeline 模型目录；如果仍然不可用，则自动回退到项目原有的 PDF 提取路线。
