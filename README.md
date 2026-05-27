# MAS-LiteBus

面向多智能体协作的低开销通信、非文本状态传递与共享记忆原型系统。

本项目实现了赛题要求的完整可运行原型：

- 4 个 Agent 协作：Planner、Retriever、Executor、Summarizer。
- 同时支持六种协作模式 (覆盖 2×2 消融 + IPC 真实落地)：
  - `text` — 文本累积上下文基线 (最坏情况通信开销)
  - `text_v2` — 文本合理基线 (每步只传上一步产出)
  - `text_with_memory` — NL + 共享记忆 (消融: memory 单独贡献)
  - `protocol_no_memory` — 结构化协议 (消融: 协议单独贡献)
  - `protocol` — 结构化协议 + 共享记忆 (in-process)
  - `protocol_ipc` — 结构化协议 + 真实多进程 IPC + 共享内存: 4 个 Agent 在独立子进程, 协议消息走 AF_UNIX socket, embedding 向量通过 `multiprocessing.shared_memory` 跨进程传引用
- 使用哈希语义向量作为非文本中间状态，在 Agent 间通过 `state_ref` 或 `shm_name` 传递。
- 使用 SQLite (WAL) 共享记忆库，支持关键词、标签和语义相似度检索，跨进程并发读写。
- 内置 2 组关联连续任务，共 10 轮，并生成四模式对比报告。

## 快速运行

跑全部六种模式, 每个重复 5 次取均值与标准差, 生成 6 列对比 + 消融归因 + 记忆准确性报告:

```bash
python scripts/run_benchmark.py --mode all --rounds 10 --repeat 5
```

只跑一种模式 (排障或单独基线):

```bash
python scripts/run_benchmark.py --mode protocol_ipc --rounds 10
```

运行后会在 `outputs/` 下生成：

- `benchmark_summary.json` — 含 metrics / metrics_std / 记忆准确性 / 消融对比
- `benchmark_report.md` — 自动渲染的 Markdown 对比报告
- `memory_<mode>.sqlite3` — 每种模式一份独立记忆库, 跨任务复用

## 测试

```bash
python -m unittest discover -s tests
```

## 项目结构

```text
mas_litebus/
  agents/      Agent 实现 (Planner / Retriever / Executor / Summarizer)
  runtime/     in-proc 协议运行时 (engine.py) 与跨进程 IPC 运行时 (ipc_engine.py)
  ipc/         AF_UNIX socket bus + multiprocessing.shared_memory 状态池
  state/       哈希语义向量 + 状态对象
  memory/      SQLite (WAL) 共享记忆存储与检索
  eval/        指标 / 评测 / 6 列对比报告 / 记忆准确性 (P@3, R@3, F1, MRR)
tasks/         10 轮连续任务
scripts/       命令行入口
docs/          设计文档、部署文档、实验报告、演示脚本
tests/         单元测试 (核心 + IPC)
```

## openEuler 适配

项目核心仅依赖 Python 标准库。评审环境只需安装 Python 3.9+ 即可运行，推荐 Python 3.11。

