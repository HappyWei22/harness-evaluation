# 「复制一行」运行证据

## 观察

2026 年 11 月 19 日，北京时间 14:45 发起 v2 正式请求。隔离预检通过后，DeepSeek Harness（DSH）接收了 1 条正式题面并完成第 1 轮。宿主取回与输入逐字节相同的 16 字节文件。题后恢复时，Windows 重用了启动事件编号，初次核验报失败。原件显示事件时间和系统开机时间均已更新，复核后于 14:53 补关账。

| 活动与编号 | 结果 | 原件公开副本 |
| --- | --- | --- |
| 正式运行 `TASK-cl-v1-formal-20261119T1445-bf1774c3` | 正式 1、诊断 0。结果文件通过，恢复经原件复核后关账。 | [正式记录](formal-v2/request-state.json) · [原生事件](formal-v2/native-events.jsonl) · [账本摘录](ledger-query.json) |
| 正式运行前的隔离预检 `PRE-1dd06167754b419b` | 4 个地址的 8 个共享端口均不可达，两个已登记文件均无法打开。产品提交 0。 | [预检记录](formal-v2/isolation-preflight.json) |
| v2 附着诊断 `TASK-cl-v1-postfix-20261119T0044-8528528d` | 使用已有桌面，发送 1 条诊断并完成，产物正确。正式提交 0。 | [宿主报告](diagnostic-v2/host-report.json) · [原生事件](diagnostic-v2/native-events.jsonl) · [关账状态](diagnostic-v2/request-state.json) |
| 旧读取门禁诊断 `ISO-2c46b1662009465f`，关联请求 `cl-v1-formal-20261119T1239` | 授权样本读取成功，宿主标记返回原因不明的路径错误。诊断 1，正式 0。 | [原生事件](legacy-gate-block/native-events.jsonl) · [拦截裁定](legacy-gate-block/isolation-decision.json) |
| 启动请求 `cl-v1-zero-preflight-20261119T1430` | 发现已有 DSH 进程，提交 0。旧收尾仍应用了快照，恢复核验失败。 | [宿主报告](startup-guard-before-fix/host-report.json) · [题后报告](startup-guard-before-fix/restore-final.json) |
| 隔离预检 `PRE-14065b2c46494cba` 与同次启动的复查 `PRE-SMB-20261119T143506` | 初次预检阻断。复查发现另一块网卡的 TCP 139 和 445 端口可达。产品提交均为 0。 | [初次预检](port-block/isolation-preflight.json) · [同启动复查](port-block/share-recheck.json) |
| v3 桌面预检 `PRE-DESKTOP-20261119T075258-f1d4582b` | 恢复含自动登录配置的快照，进入控制台桌面，打开 DSH 并聚焦输入框。产品提交 0。 | [桌面预检](desktop-v3/preflight-report.json) · [快照恢复回执](desktop-v3/restore-first.json) |

正式运行与两次诊断使用 DSH 原生请求记录的 `deepseek-flash`，推理档位 `high`。原生权限记录为 `workspace-write` 和 `ask`，即限制写入范围、需要时请求批准。宿主通过 Hyper-V、PowerShell Direct（宿主到来宾的本地管理连接）与 Windows 界面自动化执行调度。题目版本、工具、权限、时间和裁定可在各行链接中核对。DSH 安装版本只留有 `0.2.2.0` 的参考值，实跑未现场查证。技能启用状态未采集。预检的模型和技能不适用。

## 证据

- **E1 结果证据。** [题面](formal-v2/prompt.txt)、[输入](formal-v2/input.txt)、[结果](formal-v2/result.txt)与[最终回复](formal-v2/final-reply.txt)保留原字节。输入和结果的 SHA-256 指纹均为 `f43e45bdbdfb122598953e76c387da1df0c3d526e3594175fd7849ba128f3127`。
- **E2 过程证据。** [原生事件](formal-v2/native-events.jsonl)中，序号 8 是全文匹配的操作者消息。之后依次发生 `read → write → read`，序号 40 是第 1 轮的 `completed` 终态。[接收](formal-v2/receipts/accepted.json)、[终止](formal-v2/receipts/terminated.json)、[归档](formal-v2/receipts/archived.json)和[清理](formal-v2/receipts/cleaned.json)四份回执对应同一尝试。
- **E2 恢复与关账。** [首次启动](formal-v2/restore-first.json)和[题后恢复](formal-v2/restore-final.json)均记录事件编号 1060。UTC 事件时间从 06:45:33.6514576 前进到 06:47:56.6700500，题后系统开机时间为 06:47:59.3313000，且本轮工作区、启动器和会话均无残留。[补核验](formal-v2/restore-recheck.json)保留原失败原因和原件指纹。[账本查询摘录](ledger-query.json)显示同一尝试有一次提交、一次许可消耗和关账时间。

## 解释

两次不同的拦截记录说明门禁为什么需要具体的失败原因。旧门禁收到 `FS_IO_ERROR: UNKNOWN` 后保留现场。新门禁的阻断轮出现可达端口，直到后续正式轮的预检确认所有受检端口不可达、受保护的两份文件均返回 Windows 错误码 `0x80070035`（找不到网络路径），才放行。两轮的条件有变化，因此只用它们解释对应轮次的裁定。

恢复代码修改为结合事件时间与系统开机时间复核。现有正式轮使用封存原件补关账。代码修订、自测与历史影响见[修订留存](../../tools/windows-single-task/versions/README.md)。

## 未排除因素

- 隔离结论覆盖当轮测试的 Windows 共享通道和两份测试文件。阻断轮只确认额外网卡上两个端口可达，该地址能否打开文件仍未知。核查止于当轮保存的探针和同启动复核。
- 版本、技能和正式 v2 运行中的登录操作者未知，限制跨版本、技能影响和无人值守的比较。本次发布核查止于既有原件。
- 修正后的恢复判断有离线和本轮原件复核。尚无另一次实机运行验证修正后从发送到自动关账的完整路径。v3 的实机证据止于输入框就绪。以上记录不用于跨产品正式比较。

## 可复现实验

在仓库根目录执行 `python -B evidence/windows-single-task-2026-11-19/verify.py`。核对程序读取公开副本，检查文件指纹、同轮提交与终态、工具顺序、产物、恢复与关账，另外核对两个阻断和 v3 桌面预检。它不启动虚拟机或重新答题。用 [Windows 单题工具](../../tools/windows-single-task/README.md)另开实机请求时，应登记新 ID。v2 正式轮、附着诊断、Windows 接口预检和离线自测各自计数。

[来源与指纹](source-index.json)逐件列出宿主原件路径（相对 Control 目录）、原件 SHA-256 和公开副本 SHA-256。JSON 与原生事件摘录移除了主机账号、真实网络地址和无关上下文。运行 ID、原生事件序号、时间、关键字段和安全的结果文件保留。完整原件可在有权读取原始账本的环境中按指纹复核。
