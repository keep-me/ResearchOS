# MinerU Runtime

这个目录只用于缓存 MinerU API 返回的 OCR 结果，不存放源码。

目录说明：

- `runtime/`: 论文 OCR 结果缓存

当前项目通过 `packages/ai/paper/mineru_runtime.py` 调用 MinerU 官方 API，拿到结果包后解压到 `runtime/` 目录，供 OCR 证据和图表提取复用。
