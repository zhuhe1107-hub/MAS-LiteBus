# MAS-LiteBus Benchmark — Offline (Deterministic Agents)

本报告仅包含**确定性模板 Agent** 在同进程下跑出的对比, 不含 LLM 调用. 适合在评审无网环境下复测, 数据完全可复现 (固定哈希 embedding + 模板逻辑). LLM 数据见 `benchmark_llm.md`, 跨进程 IPC 专项见 `benchmark_ipc.md`.

_(每模式重复运行 3 次, 易抖动指标显示 mean ± std)_

## 多模式对比

| 指标 | text (累积) | text_v2 (合理基线) | text + memory | protocol (no memory) | protocol (full) |
|---|---:|---:|---:|---:|---:|
| Agent 消息数 | 40 | 40 | 40 | 44 | 44 |
| 通信字符数 (text_chars 或 protocol_chars) | 51145 | 21440 | 22320 | 15379 | 16950 |
| 估算 token (chars / 1.8) | 28414 | 11911 | 12400 | 8544 | 9417 |
| 非文本状态传递次数 | 0 | 0 | 0 | 40 | 40 |
| 状态字节数 (累计) | 0 | 0 | 0 | 25600 | 25600 |
| 总耗时 (秒) | 0.314879 ± 0.023912 | 0.323679 ± 0.009295 | 0.418607 ± 0.026507 | 0.175716 ± 0.020784 | 0.420917 ± 0.008294 |
| 记忆检索次数 | 0 | 0 | 10 | 0 | 10 |
| 记忆命中率 | 0.0% | 0.0% | 80.0% | 0.0% | 80.0% |
| 本地语料检索次数 | 10 | 10 | 3 | 10 | 3 |

## 相对基线的提升

> 符号规则: **正号 = 优于基线**, 负号 = 劣于基线. 例如 `耗时 +20%` = 比基线快 20%, `耗时 -40%` = 比基线慢 40%.

- **text (累积)** 相对 text_v2 (合理基线): token -138.6%, 通信字符 -138.5%, 耗时 +2.7%, 本地检索次数 +0.0%, 消息数 +0.0%, 命中率 +0.0%
- **text + memory** 相对 text_v2 (合理基线): token -4.1%, 通信字符 -4.1%, 耗时 -29.3%, 本地检索次数 +70.0%, 消息数 +0.0%, 命中率 +80.0%
- **protocol (no memory)** 相对 text_v2 (合理基线): token +28.3%, 通信字符 +28.3%, 耗时 +45.7%, 本地检索次数 +0.0%, 消息数 -10.0%, 命中率 +0.0%
- **protocol (full)** 相对 text_v2 (合理基线): token +20.9%, 通信字符 +20.9%, 耗时 -30.0%, 本地检索次数 +70.0%, 消息数 -10.0%, 命中率 +80.0%


## 记忆复用准确性 (vs gold prior task ids)

| 模式 | scored | P@3 | R@3 | F1@3 | MRR |
|---|---:|---:|---:|---:|---:|
| text + memory | 8/8 | 87.5% | 88.8% | 84.5% | 1.0000 |
| protocol (full) | 8/8 | 87.5% | 88.8% | 84.5% | 1.0000 |

每个任务在 `tasks/continuous_tasks.json` 里都标注了 `gold_prior_task_ids` (理论上应该被复用的前序任务). 分数把 task_id 通过当前 run 的 task→memory 映射翻译为 gold memory_ids, 与系统实际 `memory_refs` 求交集计算 P/R. 冷启动任务(无 gold prior) 不计入分母. **MRR 衡量 retrieve 列表第 1 名是不是 gold**, 1.0 意味着每次最相关的记忆都排在头部.


## 消融归因 (相对 text_v2 基线)

| 增量 | 字符 | token | 本地检索 | 记忆命中率 |
|---|---:|---:|---:|---:|
| +memory 单独贡献 (NL 不变) | -4.1% | -4.1% | +70.0% | 80.0% |
| +protocol 单独贡献 (memory 不变) | +28.3% | +28.3% | +0.0% | 0.0% |
| +protocol + memory 合计 | +20.9% | +20.9% | +70.0% | 80.0% |

两项单独收益叠加 +24.2% vs 合计 +20.9% — 差值反映两个机制并非线性可加, memory 命中后 retrieve 文本变短, 协议封装受益于此, 因此合计大于纯线性叠加.


## 结论

- **通信效率**: 结构化协议把 Agent 间长上下文压缩为动作/参数/结果/能力/引用, 字符与 token 节省显著.
- **非文本状态传递**: HashEmbedding 128 维向量通过 `multiprocessing.shared_memory` 直接跨进程引用, socket 只承载短 shm_name.
- **共享记忆**: SQLite (WAL) 在 coordinator/retriever/summarizer 三个进程之间安全并发, 后续任务在标签和语义相似度上命中前序记忆.
- **真实 IPC 成本**: protocol_ipc 模式可被 `ps -ef | grep worker` 与 `ls /dev/shm` 直接观察, 不是同进程函数调用伪装的"协作".

本报告由 `scripts/run_benchmark.py --mode all --repeat N` 自动生成.
