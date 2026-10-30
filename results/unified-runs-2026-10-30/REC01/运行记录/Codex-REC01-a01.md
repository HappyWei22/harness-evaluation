# Codex｜REC01运行记录

> 公开版：路径及私人标识已脱敏；证据公开范围见[来源说明](<../../../../docs/公开材料与来源说明.md>)。


| 项目 | 记录 |
| --- | --- |
| 唯一ID | Codex-REC01-a01 |
| 题目与脚本版本 | REC01 参数报错后继续导出；业务脚本1.0.0；评分规则1.0.0（本题判据未改）；[题面〔公开副本〕](<../../../../cases/REC01/提示词/01-开始任务.md>)／[业务脚本〔公开副本〕](<../../../../cases/REC01/输入/recovery_export.py>) |
| 产品版本 | 未从本次材料独立确认（CLI与桌面App版本未单列） |
| 模型 | gpt-6.1-sol；思考强度：高（原生日志配置记录） |
| 工具 | exec_command（两次独立调用：legacy-json失败→json更正）；目录：`…/独立测试工作区/Codex/REC01-a01` |
| 权限 | workspace-write；审批on-request（原生日志与运行状态） |
| 技能状态 | 未记录／未知（本次配置记录未含Skill声明） |
| 时间 | 2026-10-20 21:28:11.829—21:28:31.254（北京时间；原生task_started至task_complete，跨度19.425秒） |
| 证据等级 | E2支持首次退出7与第二次退出0的调用／返回配对；E1支持交付文件为3项活动39人且脚本指纹与准备时一致。[native-session.jsonl〔公开副本〕](<../../../../evidence/recovery/REC01/Codex/native.excerpt.json>)／[结果文件〔公开副本〕](<../../../../evidence/recovery/REC01/Codex/snapshot/outputs/registration_summary.json>)／[原评分〔公开副本〕](<../../../../evidence/recovery/REC01/Codex/score.json>) |
| 最终裁定 | 三项通过并纳入，3/3（原分）；首次错误由测试脚本预设，不代表产品独立定位未知故障的能力。 |

任务结论与限制见[五段式任务报告](<../REC01-五段式任务报告.md>)；[对应JSON](<Codex-REC01-a01.json>)。本次仅精简整理版，原材料与原评分保留。

公开核对补正：本地整理版原写21:28:10—21:28:31、跨度20秒；本版按原生任务起止事件补正，原值和最小时间事件见发布索引。
