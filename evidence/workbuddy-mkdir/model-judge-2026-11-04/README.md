# 第四周周报 · 目录创建评分核验包

## 11 月 20 日补充核验

[下载完整脱敏 ZIP](model-judge-verification.zip)。解压后打开 `20项核验索引.md` 可查 20 份历史请求及原样评分正文。进入解压出的 `模型评分脱敏包` 目录，运行 `python3 -B verify.py`，用冻结规则和历史校验代码离线复算。实测结果为真实观察 4/4、合成样例 13/16 一致，输出校验通过 19/20。完整核验范围与四项旧检查的封存情况见 [Issue #3 第 3 条核验](../../../docs/阶段进展/Issue-3历史评分与依赖核验-2026-11-20.md)。

先打开 [cases.json](cases.json)。里面有第四周周报使用的全部 20 项，按原编号保存。你可以任选一项核对模型看到了什么、怎么判，也可以用下文的命令重算周报的两个数字。

## 从 J011 跟读一项

在 `cases` 里找到 **J011「目录创建，缺少调用是否正常返回」**。它对应合成样例 S05。

1. **看模型收到的内容。** `model_input.prompt_ref` 指向文件顶部 `prompts` 里的评分要求。`model_input.user_json` 是这项实际发给模型的输入，其中 `observed` 显示终态存在且为目录，两项异常字段均为 `null`，但整个 `returned_normally` 键都缺失。对照 J013「目录创建，调用未正常返回且缺少异常详情」的 `false` 和 J001「目录创建，不存在的目标成功创建」的 `true`，就能看到缺失、否和是三种情况在包里的写法。
2. **看模型原答。** `model_output` 保存原答标签、分数、理由和引用。J011 给了 `PASS`、1 分，原理由是“初态与参数符合场景，调用正常返回，目标终态为存在的目录。”它还在 `evidence` 里引用了 `observed.returned_normally=true`。
3. **看参照怎么判。** `reference.verdict` 是运行前保存的标签 `UNKNOWN`。`prompt_step` 指向评分要求第 5 条，要求记录调用是否正常返回。终态能说明目录存在，无法补足调用结果。`frozen_rule_reason` 保留当时规则程序的理由“调用结果字段不完整”，`basis` 则把规则和这一项的输入对应起来。
4. **核对分歧。** `difference` 说明原答 `PASS` 与参照 `UNKNOWN` 的差异。`validation` 还记录了另一项检查，模型引用的键在实际输入中找不到，历史汇总把该输出记为 `JUDGE_ERROR`。两个问题都发生在 J011，合成组只计一次标签分歧。

查看其他样例也走这四步。`reference.source_ref` 和 `reference.rule_ref` 分别指向顶部的参照来源和规则版本。样例名称、来源和参照标签都在 `model_input` 之外，当时发给模型的是评分要求和匿名输入。

`PASS` 表示符合目录创建约定，`FAIL` 表示约定适用、证据足够但结果未达到要求。`UNKNOWN` 表示证据不足或互相矛盾，`INVALID` 表示超出适用场景或字段类型不合规，两者都记空分。`JUDGE_ERROR` 记录的是模型输出校验失败。

## 自己复算周报数字

先按 `group` 将样例分为真实观察（`historical`）和合成边界样例（`synthetic`）。对每一项比较 `model_output.verdict` 和 `reference.verdict`，相同计 1 项。历史汇总还要求输出通过格式与字段引用检查，分母仍为该组全部样例。两种口径在这批数据上的结果相同。

| 来源 | 原答标签与参照一致 | 历史汇总口径 | 第四周周报 |
| --- | ---: | ---: | ---: |
| 真实观察 | 4/4 | 4/4 | 4/4 |
| 合成边界样例 | 13/16 | 13/16 | 13/16 |

复算应列出三项标签分歧。J006「目录创建，目标为普通文件」把 `exists=true`、`is_dir=false` 的普通文件占位判为 `PASS`，参照按第 2 条判 `INVALID`。J010「目录创建，缺少目标是否存在的初态」在 `initial_state.exists` 缺失时判 `PASS`，参照按第 1 条判 `UNKNOWN`。这两项引用的字段和值都能在输入里找到。J011 是唯一校验不合格项，因此输出格式与字段引用共 19/20 项合格。

把 `README.md` 和 `cases.json` 复制到一个空目录，在该目录运行下面这条命令。它只需 Python 3，会检查样例编号和包内引用，按原答和参照重算两种口径，并检查引用字段的存在性、值和类型。

```bash
python3 -B - <<'PY'
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

pack = json.loads(Path("cases.json").read_text(encoding="utf-8"))
rows = pack["cases"]
assert len(rows) == 20
assert {row["case_id"] for row in rows} == {f"J{n:03}" for n in range(1, 21)}
assert len({row["source_sample_id"] for row in rows}) == 20
sizes = Counter(row["group"] for row in rows)
assert sizes == {"historical": 4, "synthetic": 16}
scores = {"PASS": 1, "FAIL": 0, "UNKNOWN": None, "INVALID": None}
blocks = {"initial_state", "arguments", "observed"}
missing = object()
raw_matches, usable_matches = Counter(), Counter()
disagreements, rejected = [], []

def read_field(document, path):
    for key in path.split("."):
        if not isinstance(document, dict) or key not in document:
            return missing
        document = document[key]
    return document

for row in rows:
    case_id, group = row["case_id"], row["group"]
    model_input, output, reference = row["model_input"], row["model_output"], row["reference"]
    document = model_input["user_json"]
    prompt = pack["prompts"][model_input["prompt_ref"]]
    rule = pack["rules"][reference["rule_ref"]]
    source = pack["reference_sources"][reference["source_ref"]]
    assert prompt["role"] == "system"
    assert sha256(prompt["text"].encode("utf-8")).hexdigest() == prompt["sha256_utf8"]
    assert any(line.startswith(f'{reference["prompt_step"]}. ') for line in prompt["text"].splitlines())
    assert rule["version"] == reference["rule_ref"] and source["group"] == group
    assert document["case_id"] == case_id and set(document) <= blocks | {"case_id"}
    assert row["task_name"] and reference["basis"] and reference["frozen_rule_reason"]

    assert set(output) == {"case_id", "verdict", "score", "reason", "evidence"}
    assert output["case_id"] == case_id
    actual, expected = output["verdict"], reference["verdict"]
    for label, score in ((actual, output["score"]), (expected, reference["score"])):
        assert type(score) is type(scores[label]) and score == scores[label]
    assert expected == reference["frozen_rule_verdict"]
    assert isinstance(output["reason"], str) and output["reason"].strip()
    assert isinstance(output["evidence"], list) and output["evidence"]

    bad_paths = []
    for cite in output["evidence"]:
        assert set(cite) == {"path", "value"}
        path, value = cite["path"], cite["value"]
        assert path.split(".")[0] in blocks
        found = read_field(document, path)
        if type(found) is not type(value) or found != value:
            bad_paths.append(path)
    passed = not bad_paths
    assert passed == row["validation"]["passed"]
    assert row["validation"]["comparison_verdict"] == (actual if passed else "JUDGE_ERROR")
    if bad_paths:
        rejected.append((case_id, row["task_name"], bad_paths))
    if actual == expected:
        raw_matches[group] += 1
        if passed:
            usable_matches[group] += 1
    else:
        disagreements.append((case_id, row["task_name"], actual, expected))

for group, name in (("historical", "真实观察"), ("synthetic", "合成边界样例")):
    print(f"{name}  原答 {raw_matches[group]}/{sizes[group]}，历史汇总 {usable_matches[group]}/{sizes[group]}")
for case_id, name, actual, expected in disagreements:
    print(f"标签分歧  {case_id} {name}  {actual} → {expected}")
for case_id, name, paths in rejected:
    print(f"引用不合格  {case_id} {name}  {', '.join(paths)}")
print(f"输出格式及引用合格 {len(rows) - len(rejected)}/{len(rows)}")
PY
```

## 模型、规则和参照从哪里来

2026 年 11 月 4 日的 20 项请求和响应均保存于项目的历史记录，每项请求一次。请求名称和响应返回名称都是 `deepseek-flash`。实际请求的系统提示词逐份与运行前冻结的 `mkdir-model-judge-1.0` 核对。固定规则程序为 `mkdir-semantics-1.1`。在项目历史目录“模型评分对照-v1”中，`results/model-first` 保存原请求、响应和输出校验，`bundles/first` 保存冻结清单及参照，`results/comparison-first` 保存旧汇总和核对记录。

- **4 项真实观察。** 来自两次 WorkBuddy 目录诊断，每次含两个观察。原参照由同一助手依据两次已保存的诊断正文事先指定，版本为 `mkdir-reference-1.0`。
- **16 项合成边界样例。** 参照是创建样例时预先填写的期望标签，版本为 `mkdir-fixtures-1.1`。

准备时，固定规则逐项核对过上述参照。原验收由同一助手读取 20 份接口响应，逐项对照实际输入与冻结参照。外部独立复核未执行，下方的本地复算也按编制方自查登记。

## 脱敏后还能追到哪里

| 涉及样例 | 留在本地的内容 | 影响 |
| --- | --- | --- |
| J003「目录创建，已有目录时抛出异常（交换诊断）」和 J009「目录创建，已有目录时抛出异常（基线诊断）」 | 异常正文中的真实目标路径，在当时组装模型请求前就已替换成 `[TARGET]`。 | 读者能检查异常字段和模型的引用，真实路径及其对应的历史工作区需要查看原件。 |
| 上述两项，加上 J015「目录创建，不存在的目标成功创建（交换诊断）」和 J016「目录创建，不存在的目标成功创建（基线诊断）」 | 原生会话全文、会话及调用定位信息和工作区配置。 | 读者能核对模型收到的结构化记录，从原生记录重新提取初态、参数和终态则需要历史档案。产品当时的完整权限和技能配置在历史记录中仍是未知。 |
| 全部 20 项 | 原始接口消息的序列化细节、请求标识、服务指纹、用量计数、单次调用元数据及凭据配置来源路径。冻结清单和规则源码原件也留在本地。 | 包内的输入与原答可以逐字段核对。逐字节认证接口原件或核实源码指纹需要原件。现有记录只能确认服务返回的模型名称，精确内部版本未知。 |

本包的复算范围止于这 20 条留存记录。四项真实观察来自同类诊断，合成样例用于开发检查，每项只请求了一次。模型的重复性和跨任务准确率超出这批记录能回答的范围。

## 本地复算记录

| 运行字段 | 实际记录 |
| --- | --- |
| 唯一 ID | `MKDIR-P0-RECOUNT-20261119T172604647470` |
| 活动类型 | 离线复算，编制方自查 |
| 题目版本 | `mkdir-model-judge-1.0`，关联规则 `mkdir-semantics-1.1` |
| 产品版本 | 本次未运行产品。真实观察来源为 WorkBuddy 5.8.3 |
| 模型 | 本次无模型调用。历史评分模型为 `deepseek-flash` |
| 工具 | Python 3.12.6 标准库、Bash |
| 权限 | 核对命令仅读取临时目录内交付文件，无网络请求 |
| 技能状态 | 核对命令未加载技能 |
| 运行时间 | 2026-11-19 17:26:04.647470–17:26:04.700047（UTC+08:00） |
| 证据等级 | E1（结果证据）。依据为包内逐项记录和隔离目录的命令输出，支持本批一致数和引用检查结果 |
| 最终裁定 | 20 个编号及包内引用检查通过，复算与周报一致。原评分与原记录保留 |
| 关联历史记录 | 裸模型探针 `JUDGE-MODEL-20261104T231615455235`，历史离线对照 `JUDGE-COMPARE-20261104T231645071471` |
