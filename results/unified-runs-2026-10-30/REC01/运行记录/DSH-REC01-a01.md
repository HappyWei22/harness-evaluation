# DSH｜REC01运行记录

> 公开版：路径及私人标识已脱敏；证据公开范围见[来源说明](<../../../../docs/公开材料与来源说明.md>)。


| 项目 | 记录 |
| --- | --- |
| 唯一ID | DSH-REC01-a01 |
| 题目与脚本版本 | REC01 参数报错后继续导出；业务脚本1.0.0；评分规则1.0.0；[题面〔公开副本〕](<../../../../cases/REC01/提示词/01-开始任务.md>)／[业务脚本〔公开副本〕](<../../../../cases/REC01/输入/recovery_export.py>) |
| 产品版本 | 0.2.1-rc.2（运行后读取客户端版本） |
| 模型 | deepseek-flash；推理等级：high（原生日志） |
| 工具 | bash两次＋read＋present；目录：`…/独立测试工作区/DSH/REC01-a01` |
| 权限 | workspace-write；审批ask（原生日志与运行状态） |
| 技能状态 | 未记录／未知（原生Skill目录被注入，但无显式加载调用） |
| 时间 | 2026-10-20 20:49:59—20:50:15（北京时间；原生会话首末事件，跨度17秒） |
| 证据等级 | E2支持退出7→更正→退出0与job/settled事件的配对；E1支持交付文件为3项活动39人且脚本指纹一致。外层isError=false不等于成功。[session.v4.jsonl〔公开副本〕](<../../../../evidence/recovery/REC01/DSH/native.excerpt.json>)／[结果文件〔公开副本〕](<../../../../evidence/recovery/REC01/DSH/snapshot/outputs/registration_summary.json>)／[原评分〔公开副本〕](<../../../../evidence/recovery/REC01/DSH/score.json>) |
| 最终裁定 | 三项通过并纳入，3/3（原分）；数值退出码取自对应job/settled事件，不只看外层状态。 |

任务结论与限制见[五段式任务报告](<../REC01-五段式任务报告.md>)；[对应JSON](<DSH-REC01-a01.json>)。本次仅精简整理版，原材料与原评分保留。
