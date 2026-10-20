# 失败恢复维度：本轮结果

实验日期2026-10-20，公开整理2026-10-21。基础[REC01「参数报错后继续导出」](../cases/REC01/README.md)3分，复杂[REC02「批量导出中断后续做」](../cases/REC02/README.md)12分。五款各两题，共10次用例运行、75个评分条目；不是75次独立运行。统计数据见[JSON台账](failure-recovery.json)。

| 产品 | 基础3分及证据 | 复杂12分及证据 | 本维度额度15 |
|---|---|---|---|
| Codex | [确认3，未知0](../evidence/recovery/REC01/Codex/score.json) | [确认12，未知0](../evidence/recovery/REC02/Codex/score.json) | 确认15，未知0 |
| DSH | [确认3，未知0](../evidence/recovery/REC01/DSH/score.json) | [确认12，未知0](../evidence/recovery/REC02/DSH/score.json) | 确认15，未知0 |
| WorkBuddy | [确认3，未知0](../evidence/recovery/REC01/WorkBuddy/score.json) | [确认12，未知0](../evidence/recovery/REC02/WorkBuddy/score.json) | 确认15，未知0 |
| 豆包工作 | [确认3，未知0](../evidence/recovery/REC01/Doubao/score.json) | [确认12，未知0](../evidence/recovery/REC02/Doubao/score.json) | 确认15，未知0 |
| 元宝 | [确认0，未知3](../evidence/recovery/REC01/Yuanbao/score.json) | [确认7，未知5](../evidence/recovery/REC02/Yuanbao/score.json) | 确认7，未知8 |

“确认0、未知3”表示目前没有可确认的评分项，绝不是已判0分。元宝本维度确认7、未知8，不按0处理，不按已评比例换算，也不据此排名。所有formal_score仍为空，先行试测分单独记录，不是完整100分总成绩。

## 文件正确，和过程能证明，是两回事

五款REC01最终交付都是3活动、39人；REC02都是6活动、70人。复杂题五款A的前后字节均一致，B/C及总索引符合固定标准。本轮没有在最终产物上区分出优劣，不能为了形成排名把缺证据算成失败。

Codex、DSH、WorkBuddy有原生调用与结果摘录；豆包有评测者从客户端实际展开的工具卡片整理的摘录，过程置信中；元宝可见命令、前后状态及事件，但原生工具结果正文没取得。界面摘录是评测者整理，不是后台日志原件；状态和事件由题目脚本产生，不是Harness原生日志。

元宝REC02最初仅收到四个文件，确认5项；补齐完整恢复快照后，新增确认“原任务编号保留”“未重新生成A”，到7项。剩余未知：P4实际处理B残留、P5清理范围正确、P8原会话内恢复可执行、P11实际验证覆盖充分、P12验证反馈与独立检查一致。快照中半成品不存在已经确认，但原规则还要求对应的原生工具返回，不能事后放宽条件给满分。

## 怎样复核

每款评分JSON的public_evidence指向实际公开文件，source_evidence_locations保留原材料位置及行号，仅供追溯；没有附完整日志。原生摘录保留source_line、原文件哈希和总行数。输入程序、业务产物、前后状态与任务事件保持原字节，便于独立比较。

在仓库根目录运行 `python3 checks/verify_recent.py`，或完整运行 `python3 checks/verify.py`。只读取归档，检查格式、实际数据、指纹、事件顺序、调用及结果；不会运行产品或输入程序。脚本verify的10项、独立文件检查的13项、本题评分12项不是同一计数。

## 归因与限制

首次错误是脚本主动设计的，不是发现产品Bug。模型仍决定怎样更正、先做什么、何时验证；原生Skill、默认指令和环境也可能影响行为。固定数据、错误、接口和检查标准减少这些因素影响，没有把它们完全消除。

REC02由用户按协议暂停、取证、再发继续指令，不代表自主不中断恢复或崩溃重启恢复。WorkBuddy还额外写入自己的工作日志，原记录已保留，本题未临时增加扣分。一次成功不能证明长期可靠；观察到的行为不能全部归功于Harness。
