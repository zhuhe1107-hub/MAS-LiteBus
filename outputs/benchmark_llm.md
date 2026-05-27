# MAS-LiteBus Benchmark — LLM (Ollama llama3:8b)

本报告所有模式都接入 **Ollama llama3:8b**, token 计数为 Ollama `/api/chat` 报回的真实 BPE 数 (`prompt_eval_count` / `eval_count`), 不是 chars/1.8 估算. **不包含 `protocol_ipc` 模式** — IPC worker 子进程暂未集成 LLM 后端 (httpx client 在 fork 后状态冲突), 因此把它放在 `benchmark_ipc.md` 里跟模板 protocol 对比, 避免不公平 latency 比较.

**关于 `llm_parse_failures`**: 该指标统计的是开源小模型 (llama3:8b Q4_0) 偶尔不严格遵守 「只输出 JSON」指令的次数, 例如包了一层 markdown 代码块或前面加了一句解释. 系统在 `mas_litebus/agents/*.py` 的每个 Agent 入口都用 try/except 兜底, **解析失败时自动降级到确定性模板逻辑**, 任务不会丢失, 流水线不会中断 (这一行为也包含在 11 个单元测试中). 所以 parse_failure 反映的是「LLM 输出格式稳定性」, 而不是「系统失败率」. 实测把失败率压到 0 需要换更大模型 (Qwen2.5-32B / Llama3.3-70B) 或专门的 fine-tune; 我们试过 Ollama 的 `format=json` 强约束模式, 在 llama3:8b 上单次延迟从 3s 涨到 13s 但失败率没显著下降, 因此最终选择: 保留强格式提示 + 容错 JSON 抽取 + 失败自动回退到模板, 不开 `format=json`.

## 多模式对比

| 指标 | text (累积) | text_v2 (合理基线) | text + memory | protocol (no memory) | protocol (full) |
|---|---:|---:|---:|---:|---:|
| Agent 消息数 | 40 | 40 | 40 | 44 | 44 |
| 通信字符数 (text_chars 或 protocol_chars) | 41986 | 21833 | 24990 | 15130 | 16937 |
| 估算 token (chars / 1.8) | 23326 | 12129 | 13883 | 8406 | 9409 |
| 非文本状态传递次数 | 0 | 0 | 0 | 40 | 40 |
| 状态字节数 (累计) | 0 | 0 | 0 | 25600 | 25600 |
| 总耗时 (秒) | 99.537885 | 111.561471 | 103.233169 | 66.827613 | 61.079684 |
| 记忆检索次数 | 0 | 0 | 10 | 0 | 10 |
| 记忆命中率 | 0.0% | 0.0% | 80.0% | 0.0% | 80.0% |
| 本地语料检索次数 | 10 | 10 | 3 | 10 | 3 |
| **--- LLM 真实 token (Ollama 报回) ---** | — | — | — | — | — |
| LLM 调用次数 | 40 | 40 | 33 | 40 | 33 |
| LLM prompt tokens (真 BPE) | 17660 | 16262 | 18357 | 10190 | 8541 |
| LLM completion tokens | 5329 | 6136 | 5577 | 3525 | 2996 |
| LLM 总 token | 22989 | 22398 | 23934 | 13715 | 11537 |
| LLM 平均单次延迟 (ms) | 2474.0 | 2778.3 | 3110.8 | 1659.1 | 1701.0 |
| LLM 解析失败 (回退到模板) | 11 | 11 | 9 | 10 | 7 |

## 相对基线的提升

> 符号规则: **正号 = 优于基线**, 负号 = 劣于基线. 例如 `耗时 +20%` = 比基线快 20%, `耗时 -40%` = 比基线慢 40%.

- **text (累积)** 相对 text_v2 (合理基线): token -92.3%, 通信字符 -92.3%, 耗时 +10.8%, 本地检索次数 +0.0%, 消息数 +0.0%, 命中率 +0.0%
- **text + memory** 相对 text_v2 (合理基线): token -14.5%, 通信字符 -14.5%, 耗时 +7.5%, 本地检索次数 +70.0%, 消息数 +0.0%, 命中率 +80.0%
- **protocol (no memory)** 相对 text_v2 (合理基线): token +30.7%, 通信字符 +30.7%, 耗时 +40.1%, 本地检索次数 +0.0%, 消息数 -10.0%, 命中率 +0.0%
- **protocol (full)** 相对 text_v2 (合理基线): token +22.4%, 通信字符 +22.4%, 耗时 +45.3%, 本地检索次数 +70.0%, 消息数 -10.0%, 命中率 +80.0%


## 记忆复用准确性 (vs gold prior task ids)

| 模式 | scored | P@3 | R@3 | F1@3 | MRR |
|---|---:|---:|---:|---:|---:|
| text + memory | 8/8 | 87.5% | 88.8% | 84.5% | 0.9375 |
| protocol (full) | 8/8 | 87.5% | 88.8% | 84.5% | 1.0000 |

每个任务在 `tasks/continuous_tasks.json` 里都标注了 `gold_prior_task_ids` (理论上应该被复用的前序任务). 分数把 task_id 通过当前 run 的 task→memory 映射翻译为 gold memory_ids, 与系统实际 `memory_refs` 求交集计算 P/R. 冷启动任务(无 gold prior) 不计入分母. **MRR 衡量 retrieve 列表第 1 名是不是 gold**, 1.0 意味着每次最相关的记忆都排在头部.


## 消融归因 (相对 text_v2 基线)

| 增量 | 字符 | token | 本地检索 | 记忆命中率 |
|---|---:|---:|---:|---:|
| +memory 单独贡献 (NL 不变) | -14.5% | -14.5% | +70.0% | 80.0% |
| +protocol 单独贡献 (memory 不变) | +30.7% | +30.7% | +0.0% | 0.0% |
| +protocol + memory 合计 | +22.4% | +22.4% | +70.0% | 80.0% |

两项单独收益叠加 +16.2% vs 合计 +22.4% — 差值反映两个机制并非线性可加, memory 命中后 retrieve 文本变短, 协议封装受益于此, 因此合计大于纯线性叠加.


## 结论

- **通信效率**: 结构化协议把 Agent 间长上下文压缩为动作/参数/结果/能力/引用, 字符与 token 节省显著.
- **非文本状态传递**: HashEmbedding 128 维向量通过 `multiprocessing.shared_memory` 直接跨进程引用, socket 只承载短 shm_name.
- **共享记忆**: SQLite (WAL) 在 coordinator/retriever/summarizer 三个进程之间安全并发, 后续任务在标签和语义相似度上命中前序记忆.
- **真实 IPC 成本**: protocol_ipc 模式可被 `ps -ef | grep worker` 与 `ls /dev/shm` 直接观察, 不是同进程函数调用伪装的"协作".

本报告由 `scripts/run_benchmark.py --mode all --repeat N` 自动生成.
