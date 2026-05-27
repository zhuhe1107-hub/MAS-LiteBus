# MAS-LiteBus 系统设计文档

## 1. 目标

MAS-LiteBus 面向多智能体协作中的系统层机制，验证三件事：

1. 使用结构化协议降低 Agent 间自然语言通信开销。
2. 使用 embedding 向量和状态引用实现非文本中间状态传递。
3. 使用共享记忆模块沉淀并复用跨任务经验。

系统同时支持纯文本协作模式和结构化协议协作模式，可在相同 10 轮连续任务上进行对比实验。

## 2. 架构

```text
MultiAgentRuntime
  |
  +-- PlannerAgent
  +-- RetrieverAgent
  +-- ExecutorAgent
  +-- SummarizerAgent
  |
  +-- ProtocolBus
  +-- StateStore
  +-- SharedMemoryStore
  +-- Evaluation Metrics
```

核心源码：

- `mas_litebus/runtime/engine.py`：多 Agent 运行时。
- `mas_litebus/runtime/protocol.py`：结构化通信协议。
- `mas_litebus/runtime/bus.py`：协议总线和通信统计。
- `mas_litebus/state/embedding.py`：哈希语义向量与状态池。
- `mas_litebus/memory/store.py`：SQLite 共享记忆库。
- `mas_litebus/eval/benchmark.py`：双模式评测。

## 3. Agent 设计

系统包含 4 个 Agent：

| Agent | 职责 |
|---|---|
| PlannerAgent | 任务拆解、调度计划、记忆复用决策 |
| RetrieverAgent | 本地证据检索、共享记忆检索、生成检索状态向量 |
| ExecutorAgent | 工具执行、CodeAct 风格 Python 片段、产物生成 |
| SummarizerAgent | 汇总结论、提取策略、写入共享记忆 |

每个 Agent 都暴露能力描述，协议模式下通过 `handshake` 消息上报。

## 4. 结构化通信协议

协议消息包含：

- `action`：动作类型。
- `params`：输入参数。
- `result`：返回结果。
- `capability_required`：能力需求或能力描述。
- `state_refs`：非文本状态引用。
- `memory_refs`：共享记忆引用。

示例：

```json
{
  "msg_id": "msg-xxx",
  "task_id": "A1",
  "from": "retriever",
  "to": "executor",
  "action": "retrieve.result",
  "params": {
    "query": "部署 Python Web 服务",
    "top_k": 3
  },
  "result": {
    "items": [
      {
        "title": "openEuler Python service deployment",
        "tags": ["openEuler", "Python", "deployment"]
      }
    ]
  },
  "state_refs": ["state-xxx"],
  "memory_refs": ["mem-xxx"]
}
```

## 5. 非文本状态传递

系统使用 `HashEmbedding` 将任务、证据和总结转为固定维度向量。向量不是通过自然语言长文本透传，而是存入 `StateStore`，Agent 消息只传递 `state_id`。

状态格式：

```json
{
  "state_id": "state-xxx",
  "producer": "retriever",
  "type": "embedding",
  "dim": 128,
  "dtype": "float32",
  "size_bytes": 512
}
```

生成方式：

1. 使用正则切分英文词、数字和中文单字。
2. 使用 BLAKE2b 哈希映射到固定维度桶。
3. 对向量做 L2 归一化。

后续使用：

- 与共享记忆向量计算余弦相似度。
- 作为下游 Agent 选择上下文的非文本状态引用。
- 统计状态传递次数和字节规模。

## 6. 共享记忆

共享记忆存储在 SQLite 中，每条记忆包含：

- `memory_id`
- `source_agent`
- `created_at`
- `task_topic`
- `summary`
- `tags`
- `evidence`
- `vector`
- `reuse_count`

支持：

- 关键词检索
- 标签检索
- 语义相似度检索

协议模式会在任务开始时生成任务 embedding，并执行共享记忆检索。命中记忆后，Planner 和 Retriever 会通过 `memory_refs` 复用历史经验。当命中不少于 2 条记忆时，系统会跳过本地语料重复检索，体现跨任务复用带来的计算节省。

## 7. 双模式对比

纯文本模式：

- Agent 间传递完整自然语言上下文。
- 不使用协议 `state_refs`。
- 不在任务开始阶段做共享记忆复用。

结构化协议模式：

- Agent 间传递紧凑 JSON 协议。
- 使用 `state_refs` 传递 embedding。
- 使用 `memory_refs` 复用历史记忆。

两种模式使用相同 Agent、相同任务集和相同执行逻辑。

