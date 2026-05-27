# 演示视频脚本

建议视频时长 3 到 5 分钟。

## 1. 项目简介

展示 `README.md`，说明 MAS-LiteBus 解决的问题：

- 结构化协议降低 Agent 通信开销。
- embedding 状态引用减少文本编解码。
- SQLite 共享记忆支持跨任务复用。

## 2. 架构展示

打开 `docs/design.md`，展示系统包含：

- Multi-Agent Runtime
- Protocol Bus
- StateStore
- SharedMemoryStore
- Evaluation Module

## 3. 运行实验

执行：

```bash
python3 scripts/run_benchmark.py --mode both --rounds 10
```

在本地 Windows Codex 环境可使用捆绑 Python 或已安装 Python；在 openEuler 上使用 `python3`。

## 4. 展示结果

打开：

```text
outputs/benchmark_report.md
outputs/benchmark_summary.json
```

重点讲解：

- token/字符开销下降。
- 非文本状态传递次数和数据规模。
- 共享记忆命中率。
- 本地检索次数下降。
- 总耗时改善。

## 5. 展示记忆库

说明 `outputs/memory_protocol.sqlite3` 中保存了每轮任务沉淀的记忆单元，包含：

- memory_id
- source_agent
- created_at
- task_topic
- summary
- tags
- evidence
- vector
- reuse_count

## 6. 总结

强调本项目不是简单工作流编排，而是围绕 Agent 间通信协议、状态交换和共享记忆复用实现的系统层机制。

