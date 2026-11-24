# legacy-local：仅历史调试，不再支持

这里的文件是 2026-11 之前本地 ONNX（`bge-m3-fp16-v1`）方案的入口：

- `serve.py`：旧的本地 8091 检索服务
- `build_index.py`：旧的本地 `rag/index/` 构建脚本
- `to_fp16.py` / `download_model.sh` / `gpu_smoke.py` / `verify_gpu.py`：本地 GPU 权重与冒烟工具
- `analyze_gate.py` / `verify_vs_ckpt.py`：依赖本地 ONNX `Embedder` 的离线分析脚本

当前唯一支持的 RAG/嵌入路径是 **SiliconFlow `BAAI/bge-m3`（1024 维）**：

- 服务：`rag/serve_cloud.py`（`siliconflow-bge-m3-v1`）
- 索引：`rag/index-siliconflow/`（或 `KB_CLOUD_INDEX` 指定的目录）
- 构建：`rag/rebuild_index.py`

启动器、systemd 和文档都不再引用本目录。请不要把这里的脚本重新接回启动流程：
本地 ONNX 向量与云端向量**不能混库**，索引格式和 `dim` 也必须一致。仓库不再分发
ONNX 权重，也不提供本地模型的安装/运行支持。

如果只是排查历史记录，可以直接阅读源码；运行它们产生的索引或向量不能用于当前服务。
