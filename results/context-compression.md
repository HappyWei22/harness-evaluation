# 上下文压缩：探索结果，暂不计正式分

2026-10-19开展CTX02「压缩后状态保持」，2026-10-20保留进度后转向失败恢复。见[用例、输入和原草稿规则](../cases/CTX02/README.md)、[JSON索引](context-compression.json)。

| 产品 | 内容核对 | 压缩与过程证据 | 限制 |
|---|---|---|---|
| [DSH](../evidence/context-compression/DSH/observation.json) | 16项符合 | [原生摘要](../evidence/context-compression/DSH/compaction-summary.json)与[后续基础正文选定片段](../evidence/context-compression/DSH/post-request-summary-block.json)一致 | 人工触发；不是服务端完整请求或自动压缩证明 |
| [WorkBuddy](../evidence/context-compression/WorkBuddy/observation.json) | 16项符合 | [压缩记录摘录](../evidence/context-compression/WorkBuddy/compaction-content-excerpt.json)仍含五轮原业务输入 | 不能说只靠精简摘要作答，没取得完整模型请求 |
| [Codex](../evidence/context-compression/Codex/observation.json) | 16项符合 | [压缩检查](../evidence/context-compression/Codex/compaction-check.json)及[替换历史摘录](../evidence/context-compression/Codex/replacement-history-excerpt.json) | 五轮原文仍保留，部分压缩内容不可读，没取得完整模型请求 |
| [豆包工作](../evidence/context-compression/Doubao/observation.json) | 用户提供最终内容16项符合 | 没确认压缩 | 只作内容观察；界面“自动 高”与指定模型关系待核实 |
| [元宝](../evidence/context-compression/Yuanbao/observation.json) | 用户提供最终内容16项符合 | 没确认压缩 | 只作内容观察，不补压缩过程判断 |

每款实际最终JSON与observation.json放在同一证据目录，来源层次已标明。这里是80个候选内容检查，不是80次运行，也不是五款各获得正式16分。formal_score全部为空；原CTX01基础4分结果保持原样。

三款都在较短业务历史后手动压缩，还没有验证窗口接近容量时的自然触发。各产品窗口和压缩策略不同，相同输入量不等于相同压力。因此先暂停正式复杂题评分，没有通过临时灌入更多内容凑一次压缩成功。

复核命令 `python3 checks/verify_recent.py` 比较最终JSON、DSH两处摘要片段，以及WorkBuddy/Codex保留的五轮原文。公开摘录不含系统提示、思考内容、加密块或完整模型请求；DSH选定基础正文片段也不等于已证明服务端实际接收。
