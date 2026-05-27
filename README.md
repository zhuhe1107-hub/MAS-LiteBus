# MAS-LiteBus

面向多智能体协作的低开销通信、非文本状态传递与共享记忆原型系统。

本项目实现了赛题要求的完整可运行原型：

- 不少于 3 个 Agent 协作：Planner、Retriever、Executor、Summarizer。
- 同时支持纯文本协作模式与结构化协议协作模式。
- 使用结构化协议承载动作、参数、结果、能力描述、状态引用和记忆引用。
- 使用哈希语义向量作为非文本中间状态，在 Agent 间通过 `state_ref` 传递。
- 使用 SQLite 共享记忆库，支持关键词、标签和语义相似度检索。
- 内置 2 组关联连续任务，共 10 轮，并生成通信开销、耗时、状态传递和记忆复用对比报告。

## 快速运行

```bash
python scripts/run_benchmark.py --mode both --rounds 10
```

运行后会在 `outputs/` 下生成：

- `benchmark_summary.json`
- `benchmark_report.md`
- `memory_text.sqlite3`
- `memory_protocol.sqlite3`

## 测试

```bash
python -m unittest discover -s tests
```

## 项目结构

```text
mas_litebus/
  agents/      Agent 实现
  runtime/     协议、总线、运行时
  state/       embedding 与状态交换
  memory/      共享记忆存储与检索
  eval/        评测与报告生成
tasks/         10 轮连续任务
scripts/       命令行入口
docs/          设计文档、部署文档、实验报告模板
tests/         单元测试
```

## openEuler 适配

项目核心仅依赖 Python 标准库。评审环境只需安装 Python 3.9+ 即可运行，推荐 Python 3.11。

