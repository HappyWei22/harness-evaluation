# 复核记录

> **状态**：初稿（待复核者确认个人信息后交回项目方）。
> **材料范围**：本记录只使用同一复核包内的 `01-复核说明.md`、`02-证据摘录.md`、`03-代码摘录.md`、`04-复核记录.md`。未修改上述文件中的任何一份，未重跑实验，未查阅外部资料。
> **证据分级**：**直接观察**＝材料已记录的结果字段、日志原文或异常栈原文；**代码推断**＝依据《03-代码摘录》的代码结构作出的推断；**证据不足**＝现有材料无法核实，保留标记而不补齐。
> **引用约定**：代码行号取自《03-代码摘录》，与异常栈中的行号一致；宿主日志行号为《02-证据摘录》保留的原行号；`K7`／`R3`／`M8` 三个编号仅用于引用，不是脚本的阶段标签。

## 一、复核者信息

- 复核者 复核者 A／日期：2026 年 11 月 9 日
- 与项目的关系，是否参与过原实验：
  非项目成员，受邀进行独立技术复核。
  是否参与原实验设计、运行及原报告编写：无
- 是否提前接触过原报告、结论或阶段标签：无
- 实际查阅材料：
  - `01-复核说明.md`
  - `02-证据摘录.md`
  - `03-代码摘录.md`
  - `04-复核记录.md`
    （上述四份材料的同名副本与正本逐字节相同、SHA-256 一致，据此确认为同一复核包；本次未查阅包外材料。）
- 是否接触过基于本复核包生成的第三方（含 AI）初步分析：
  复核者告知，在正式填写本记录前已阅读过一份基于本复核包生成的 AI 初步分析；具体接触范围与时间：四个md文件，2026年11月9日。
  说明：此项与"是否接触过原作者报告、原始结论或阶段标签"是两件不同的事，需分开确认；上一条的确认不因本条而自动成立，本条的具体程度亦以复核者最终确认为准。

## 二、逐次观察

### 2.1 实验基本条件（核对结果）

| 核对项             | 记录值                                                                                                                                                                                          | 出处                                     |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| WorkBuddy 版本     | 5.8.3                                                                                                                                                                                           | 02「调用及初始条件」                     |
| Python 版本        | 3.13.12                                                                                                                                                                                         | 02「调用及初始条件」                     |
| 执行命令           | `python3 probe_mkdir.py`                                                                                                                                                                      | 02「调用及初始条件」（工具请求中的命令） |
| 目标初始状态       | 已存在的空目录：`is_directory=true`、`entries=[]`                                                                                                                                           | 02 `target_initial_state`              |
| 目标是否为符号链接 | `is_symlink=false`                                                                                                                                                                            | 02 `target_initial_state`              |
| 初态检查           | `target_initial_state_matches=true`、`target_is_empty_directory=true`                                                                                                                       | 02 `checks`                            |
| 摘要检查           | `probe_sha256_matches=true`、`component_sha256_matches=true`、`installed_shim_sha256_matches=true`、`installed_backend_sha256_matches=true`（摘录只给出布尔检查结果，未给出摘要值本身） | 02 `checks`                            |
| 原始绑定检查       | `original_Path_mkdir_binding=true`                                                                                                                                                            | 02 `checks`                            |
| Broker 状态        | `broker_enabled=true`                                                                                                                                                                         | 02 `checks`                            |
| 脚本退出码         | 0                                                                                                                                                                                               | 02 末段                                  |

原生记录只有一条 Bash 请求和一条对应工具返回，宿主端日志没有 Python 脚本的阶段字段。因此三次调用记录与三段宿主日志的配对，依据的是同次工具调用、同一目标路径、三次目录操作的顺序以及时间戳先后（同一 `pid=PUBLIC_PID`，均在同一秒内，`K7` 为 `.618–.623`、`R3` 为 `.653–.656`、`M8` 为 `.658–.659`），而不是显式的阶段标识。**直接观察**：版本、命令、目标初态、各项检查的布尔结果、退出码。**证据不足**：配对无显式阶段标识，仅凭顺序与时间戳，见「问题一」第 4 小节。

### 2.2 三次调用的输入参数

| 编号 | `exist_ok` | `mode` | `parents` | 参数一致性   |
| ---- | ------------ | -------- | ----------- | ------------ |
| K7   | `true`     | `511`  | `false`   | 三次完全相同 |
| R3   | `true`     | `511`  | `false`   | 三次完全相同 |
| M8   | `true`     | `511`  | `false`   | 三次完全相同 |

三次调用在探针中的写法均为 `target.mkdir(mode=0o777, parents=False, exist_ok=True)`（02 异常栈、03 第 128 行）。`mode=511` 即 `0o777`，是传给创建操作的权限位参数。

**不得混淆的两个 `mode`**：请求参数中的 `mode=511`（`0o777`，请求的权限位）与文件系统状态中的 `mode=16877`（`0o40755`，`lstat` 的 `st_mode`，含文件类型位与权限位）是两个不同字段，不能互相比对，也不能用前者判断后者是否"被改动"。

### 2.3 逐次观察表

| 编号 | Python 返回或异常                                                                                                    | 宿主策略与文件操作结果                                                                                                                                                                    | 证据字段或日志行号                                                                                                                                                                                                                                                                              |
| ---- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| K7   | `PermissionError`，`EEXIST: file already exists`；`returned_normally=false`、`mkdir_attempts=1`              | 策略授权（`matchedRuleSource=user-allowlist`、`decision=grant-token`、`approvedByUser=false`、`result=approved`）；实际 `operation=mkdir` 返回 `ok=false`、`error=EEXIST`   | 02「K7」`returned_normally`、`exception_type`、`matching_responses`（`response_ok=false`、`response_decision=host-op`、`request_has_exist_ok=false`）；宿主日志 2346–2351（策略 2348，host-op 2349，IPC 完成 2350）；异常栈 probe 128 → intervention 55 → sitecustomize 754／501 |
| R3   | 正常返回，无异常；`returned_normally=true`、`exception_type=null`、`exception_text=null`、`mkdir_attempts=1` | 策略授权（`matchedRuleSource=user-allowlist`、`decision=grant-token`、`approvedByUser=false`、`result=approved`）；实际 `operation=mkdir` 仍返回 `ok=false`、`error=EEXIST` | 02「R3」`returned_normally`、`exception_type`、`matching_responses`（`response_ok=false`、`response_decision=host-op`、`request_has_exist_ok=false`）；宿主日志 2365–2370（策略 2367，host-op 2368，IPC 完成 2369）；该次无异常栈                                                  |
| M8   | `PermissionError`，`EEXIST: file already exists`；`returned_normally=false`、`mkdir_attempts=1`              | 策略授权（`matchedRuleSource=user-allowlist`、`decision=grant-token`、`approvedByUser=false`、`result=approved`）；实际 `operation=mkdir` 返回 `ok=false`、`error=EEXIST`   | 02「M8」`returned_normally`、`exception_type`、`matching_responses`（`response_ok=false`、`response_decision=host-op`、`request_has_exist_ok=false`）；宿主日志 2372–2377（策略 2374，host-op 2375，IPC 完成 2376）；异常栈 probe 128 → intervention 55 → sitecustomize 754／501 |

### 2.4 必须区分的两个层面：策略授权与文件操作结果

| 层面                                  | K7                                                                                 | R3                 | M8                            |
| ------------------------------------- | ---------------------------------------------------------------------------------- | ------------------ | ----------------------------- |
| 宿主策略判断（`phase=host-policy`） | `user-allowlist`／`grant-token`／`approvedByUser=false`／`result=approved` | 同左               | 同左                          |
| 宿主实际文件操作（`phase=host-op`） | `operation=mkdir`、`ok=false`、`error=EEXIST`                                | 同左               | 同左                          |
| Python 层最终结果                     | `PermissionError`（EEXIST）                                                      | 正常返回（无异常） | `PermissionError`（EEXIST） |

**宿主策略授权与宿主文件操作成功是两个不同层面的结果。** `approvedByUser=false` 不等于宿主拒绝操作：日志同时记录了 `matchedRuleSource=user-allowlist`、`decision=grant-token`、`result=approved`，表明宿主策略匹配到用户允许列表并通过授权，本次并未弹出逐次人工确认；但后续实际文件操作仍然返回 `EEXIST`、`ok=false`。

```text
策略授权成功 != 实际文件操作成功
```

R3 虽然 Python 层正常返回，但宿主端的实际文件操作仍然 `ok=false`、`error=EEXIST`。**不得把 R3 描述为宿主成功创建了目录。**

### 2.5 三次调用前后的目录状态

| 字段             | 初态                | K7 前／后 | R3 前／后 | M8 前／后 |
| ---------------- | ------------------- | --------- | --------- | --------- |
| `inode`        | 37851201            | 37851201  | 37851201  | 37851201  |
| `device`       | 16777233            | 16777233  | 16777233  | 16777233  |
| `uid`          | 501                 | 501       | 501       | 501       |
| `gid`          | 20                  | 20        | 20        | 20        |
| `mode`         | 16877               | 16877     | 16877     | 16877     |
| `mtime_ns`     | 1793171245035267298 | 同左      | 同左      | 同左      |
| `ctime_ns`     | 1793171245035285881 | 同左      | 同左      | 同左      |
| `entries`      | `[]`              | `[]`    | `[]`    | `[]`    |
| `is_directory` | `true`            | `true`  | `true`  | `true`  |
| `is_symlink`   | `false`           | `false` | `false` | `false` |

**直接观察**：三次调用的 `target_before` 与 `target_after` 十个字段逐一相同，且与 `target_initial_state` 相同；三次调用前后目标目录的类型、是否为符号链接、是否为空、inode、device、uid、gid、mode、mtime_ns、ctime_ns 均保持一致。也就是说，目标目录在三次调用前后没有发生可观察的变化。

此外，探针在每次调用前执行同一比较：若 `before != initial` 或六项绑定检查不全为真，则记录 `stop_reason='state_or_binding_changed_before_' + phase`、`exit_code = 22` 并跳出循环（03 第 117–120 行）。本次退出码为 0，说明**已记录的每个阶段在调用前都通过了这一比较**（**代码推断**，依据第 117–120 行与退出码 0）。

**不得仅凭异常信息判断目录发生了变化**：`EEXIST` 与调用后状态不变是相互印证的两条独立记录，前者不能替代后者。

### 2.6 恢复检查与脚本退出码

恢复检查（02 末段，03 第 192–197 行生成）：

```json
{
  "Path_mkdir_restored": true,
  "broker_enabled": true,
  "os_mkdir_unchanged": true,
  "sender_restored": true
}
```

其含义（**代码推断**，对照第 192–197 行）：`Path.mkdir` 已重新绑定为安装前的 `original_mkdir`、`sitecustomize._broker_send_request` 已恢复为安装前的 `original_send`、`os.mkdir` 未被替换、Broker 在脚本结束时仍处于启用状态；并且 `all(restoration.values())` 为真，因此退出码未被改为 23（第 198–200 行）。这说明**列出的这几项函数绑定恢复检查通过、Broker 仍然启用**，但不能扩大解释为整个运行环境不存在任何其他副作用（例如包装期间对 `contextvars` 的使用、事件列表的累积、以及宿主端记录本身），这些均未在恢复检查的覆盖范围内。

Bash 返回的脚本退出码为 0。**不能因为脚本退出码为 0，就认为三次 mkdir 均正常返回。** 依据代码结构，单次调用的异常在探针内部被捕获后仅做记录（03 第 130–134 行），不会中断脚本；`exit_code` 只在"调用前状态或绑定改变"（第 119 行）和"恢复失败／恢复检查失败"（第 190、199 行）时被改为非 0。因此退出码 0 只表示**脚本整体运行到结束且恢复检查通过**，不携带每次 mkdir 的返回信息（**代码推断**，退出码 0 本身为**直接观察**）。三个层面必须分开看：

| 层面                          | 本次取值                        | 判断依据                                                 |
| ----------------------------- | ------------------------------- | -------------------------------------------------------- |
| 脚本整体执行状态              | 退出码 0                        | 02 末段                                                  |
| 每次 mkdir 的 Python 返回状态 | K7 异常、R3 正常返回、M8 异常   | 各次调用记录的 `returned_normally`／`exception_type` |
| 宿主端实际文件操作状态        | 三次均 `ok=false`、`EEXIST` | 宿主日志 2349／2368／2375                                |

## 三、独立判断

### 问题一：三次调用的输入、宿主记录和目录状态是否可比？

#### 1. 输入条件

三次调用的 `exist_ok=true`、`mode=511`、`parents=false` 完全一致（2.2 节），且探针中的调用写法只有一处（03 第 128 行），三次异常栈与调用记录指向同一行、同一参数。**直接观察**：参数一致。

#### 2. 目录状态

三次调用前目标目录均为已存在的空目录、不是符号链接；调用前后十个状态字段逐一相同，并与初态相同（2.5 节）。作为独立佐证，探针在每次调用前的 `before != initial` 比较均未触发中止（2.5 节）。**直接观察**：目录状态一致且未发生可观察变化。

#### 3. 宿主记录

三次调用在宿主端记录上完全同构：

- 均匹配 `user-allowlist`（`matchedRuleSource=user-allowlist`）；
- 均获得 `grant-token`（`decision=grant-token`，与 `expectedDecision` 一致）；
- 均记录 `result=approved`，且 `approvedByUser=false`；
- 请求均为 `operation=mkdir`、同一路径、`recursive=false`，且 `request_has_exist_ok=false`；
- 均发生 `host-op`，且均返回 `ok=false`、`error=EEXIST: file already exists`；
- 均为单次请求、单次响应（每次 `matching_responses` 长度为 1，宿主日志 `received=1 responded=1 unfinished=0`）；
- 三次均在同一 `pid=PUBLIC_PID`、同一秒内按顺序完成，耗时分别为 5 ms、2 ms、0 ms。

**直接观察**：上述字段。**代码推断**：`request_has_exist_ok=false` 是由包装函数在发送时直接检查请求字典得到（03 `scoped_mkdir_intervention.py` 第 41 行），且该包装不改写请求对象（第 33、46 行），因此这一布尔值可视为对实际请求字段的直接观察，而不是观察过程的产物。

#### 4. 差异及其影响

三次调用最明显的结果差异在 Python 层：**K7、M8 抛出 `PermissionError`，R3 正常返回**。而按已记录的字段看，三次的输入参数、目标目录状态、宿主策略判断、实际文件操作结果都没有差别。也就是说，差异只出现在 Python 层，而 Python 层的差异无法由已记录的输入或宿主响应字段直接解释，只能由未记录在案、但在代码中确实存在的运行时开关（异常归一化开关）解释（详见问题二）。

相关证据缺口与可能影响判断的差异：

1. **阶段配置缺失**：`probe_mkdir.py` 展示了 `for phase, normalize, expected_normal_return in PHASES:`（03 第 107 行），但本次摘录未提供 `PHASES` 的完整定义，也未提供每个阶段的 `normalize` 值。因此无法直接核实三次调用分别配置了什么 `normalize` 值，也无法核实各阶段预期正常返回值与实际结果的对应关系。同时，02 明确说明摘录"省去了实验开关、预设答案和脚本给出的汇总判定"，因此这次差异不能反推脚本的预设判定。
2. **宿主日志为节选**：`K7` 与 `R3` 之间的第 2352–2364 行未提供，`R3` 与 `M8` 之间的第 2371 行未提供；IPC 连接标识依次为 `PUBLIC_PID-106`、`PUBLIC_PID-110`、`PUBLIC_PID-111`，首尾两段之间不连续。因此不能断言三次调用之间不存在其它宿主活动，只能说**摘录范围内**没有其它记录。
3. **配对依据是顺序**：宿主日志本身不含阶段字段（02 已说明），三次记录与三段日志的对应靠顺序与时间戳建立，属**代码推断**性质的配对，不是显式标识。
4. **`mkdir_attempts` 的口径**：三次调用记录均显示 `mkdir_attempts=1`，而探针中的该变量在循环内累加（03 第 100、127 行）；生成该字段的记录装配代码（第 140–182 行）未提供，故无法确认它是"本次调用的尝试次数"还是"累计次数"，此处只按归档原值引用，不解释为累计计数。
5. **宿主端实现未提供**：03 只提供 Python 代理侧代码，宿主如何执行 `mkdir`、是否接受 `exist_ok` 语义，材料未提供。

最终结论：三次调用在**输入参数、目标目录状态和宿主操作结果**方面具有较高可比性，且在已记录的字段上没有发现除 Python 层结果以外的差异；但由于阶段配置未完整提供、宿主日志为节选、配对依赖顺序，**不能声称所有运行时控制条件均已得到独立核实**。

### 问题二：哪段代码能解释各次调用的结果？

#### 1. 第一层：探针 `probe_mkdir.py`（执行并记录 Python 层结果）

- 第 39–46 行 `directory_state(path)`：用 `lstat()` 取 `st_dev`／`st_ino`／`st_mode`／`st_uid`／`st_gid`／`st_mtime_ns`／`st_ctime_ns`，用 `is_dir()`／`is_symlink()` 判断类型，用 `sorted(p.name for p in path.iterdir())` 取 `entries`。2.5 节的字段即来源于此。
- 第 98–121 行：创建 `ScopedMkdirIntervention()`；`intervention.install()` 安装包装；`for phase, normalize, expected_normal_return in PHASES:` 遍历阶段；每次调用前取 `before = directory_state(target)`；随后做六项绑定检查——`Path.mkdir is intervention._scoped_mkdir`、`sitecustomize._broker_send_request is intervention._observe_response`、`intervention.original_mkdir is original_mkdir`、`intervention.original_send is original_send`、`os.mkdir is original_os_mkdir`、`sitecustomize._broker_host_ops_enabled()`；不满足则中止（第 117–120 行）；满足则执行第 121 行 `intervention.normalize_existing_directory = normalize`，即把本阶段的开关值写入包装器。
- 第 123–139 行：`mkdir_attempts += 1`；执行 `target.mkdir(mode=0o777, parents=False, exist_ok=True)`（第 128 行）；正常返回则 `returned_normally = True`（第 129 行）；异常则记录 `exception_type`、`exception_text` 并把异常栈打印到 stderr（第 130–134 行）；随后取 `after = directory_state(target)`、取出本次调用新产生的事件（第 135–139 行）。

作用：执行实验并记录 Python 层的实际返回结果，即 `returned_normally`／`exception_type`／`exception_text` 与前后目录状态。

#### 2. 第二层：临时包装 `scoped_mkdir_intervention.py`（开关与异常归一化）

- 第 20–29 行 `install()`：安装前要求 Broker 已启用（第 24–25 行），并要求 `pathlib.Path.mkdir is shim._brokered_path_mkdir`，否则直接报错（第 26–27 行）。本次脚本运行到结束，说明安装时 `Path.mkdir` 确实指向 WorkBuddy 的代理函数（**代码推断**，由第 26–27 行的前置条件与退出码 0 得出）。
- 第 31–47 行 `observe_response(request)`：**总是**以原请求对象调用原始发送函数（第 33 行），**不改写任何请求或响应字段**（第 46 行）；仅当 `request['operation']=='mkdir'` 且 `request['path']` 等于本次目标时，把 7 个字段记入 `context['matching_responses']`：`request_operation`、`request_path`、`request_recursive`、`request_has_exist_ok`（`'exist_ok' in request`）、`response_ok`、`response_decision`、`response_error`。02 中的 `matching_responses` 即此结构。
- 第 49–76 行 `scoped_mkdir(...)`：建立本次调用的上下文，其中 `intervention_enabled` 直接取自 `self.normalize_existing_directory`（第 51 行），即探针第 121 行写入的阶段开关；`matching_responses=[]`、`normalized=False`。第 55 行调用 `self.original_mkdir(...)`（即 WorkBuddy 的代理函数），并把 `mode`／`parents`／`exist_ok` 原样传下去。
- 第 56–73 行异常处理：捕获 `PermissionError`，取唯一匹配的响应（第 57–58 行），再判定

```python
eligible = bool(
    exist_ok is True and parents is False
    and response.get('response_ok') is False
    and response.get('response_decision') == 'host-op'
    and isinstance(error, str) and error.startswith('EEXIST:')
    and os.path.isdir(path)
)
```

该判定要求同时满足：调用方确实传了 `exist_ok=True`、`parents=False`；宿主响应为 `ok=false` 且 `decision=host-op`；错误文本以 `EEXIST:` 开头；并且该路径当前确实是一个目录。

- 第 69–73 行：记录 `eligible_existing_directory_error`；`if eligible and context['intervention_enabled']: context['normalized']=True; return None`；否则 `raise`（重新抛出原异常，故异常栈仍指向代理函数中的原始抛出点）。
- 第 60–61 行的注释说明了设计意图：两种模式计算相同的 `eligible` 条件，**只有"符合条件的异常是否正常返回"被切换**。这正是问题一所指的那个未被摘录的运行时开关。

各标识的含义：`eligible` 表示"这次异常是否满足特殊异常处理条件"；`intervention_enabled` 表示"本次调用是否启用异常归一化"；二者同时为真时返回 `None`，即把宿主的失败响应在 Python 层吞掉、当作正常返回；否则继续抛出原异常。

#### 3. 第三层：WorkBuddy Python 代理 `sitecustomize.py`（请求宿主操作并抛出异常）

`_brokered_path_mkdir`（第 747–760 行）：

1. 计算绝对路径 `abs_path = _broker_abs_path(self)`（第 748 行）。
2. 第 750 行是原生短路分支：`if not exist_ok and os.path.exists(abs_path)` 时才直接走 `_broker_call_native_host_operation`（第 751–753 行）。本次 `exist_ok=True`，**该分支不成立**，因此请求被交给宿主。
3. 第 754–756 行构造并发送宿主操作请求：`_broker_request_host_operation("mkdir", abs_path, path=abs_path, recursive=parents, mode=mode)`。
4. 请求字段：基础字段 `id`、`command`、`runtime`、`operation`、`sessionId`（第 481–487 行），加 `request.update(fields)` 带入的 `path`、`recursive`、`mode`（第 488 行、第 755 行），再加可选的 `toolCallId`、`brokerTraceId`（第 489–492 行）。**请求中没有 `exist_ok`**；三份证据的 `request_has_exist_ok` 均为 `false`（第 41 行计算）。
5. 宿主响应 `ok=false`、`decision=host-op`（日志 2349／2368／2375，`response_ok=false`、`response_decision=host-op`）。
6. `_broker_request_host_operation`（第 475–504 行）：发送后（第 494 行）无响应则返回 `False`（第 495–496 行）；`response["ok"] is True` 则返回 `True`（第 497–498 行）；`response["decision"] == "sandbox"` 则返回 `False`（第 499–500 行）；其余情况在第 501–504 行**主动抛出 `PermissionError(response["error"] 或默认文案)`**。本次命中的就是这个分支，异常文本即宿主响应里的 `EEXIST: ...`。

两点必须写明的推论（**代码推断**）：

- 第 501 行抛出后，第 758–760 行的原生回退不会执行，因此每次调用只产生一个宿主操作请求；这与每次 `matching_responses` 长度为 1、日志 `received=1 responded=1 unfinished=0` 一致。
- 该 `PermissionError` 是代理在收到失败响应后**主动构造**的异常，不是操作系统返回的 `EACCES`／`EPERM`。仅凭异常类型不能断言发生了文件系统权限不足。

#### 4. 第四层：三次调用结果差异的形成过程

```text
probe_mkdir.py（第 128 行）
    |
    v
scoped_mkdir_intervention.py（第 55 行，临时包装）
    |
    v
sitecustomize.py _brokered_path_mkdir（第 754 行）
    |
    v
宿主文件操作请求（字段：path, recursive, mode；无 exist_ok）
    |
    v
宿主策略判断（2348／2367／2374）：grant-token，result=approved
    |
    v
实际 mkdir 操作（2349／2368／2375）：EEXIST，ok=false
    |
    v
Python 代理（第 501 行）抛出 PermissionError
    |
    v
scoped_mkdir_intervention 捕获（第 56 行），计算 eligible（第 62–68 行）
    |
    +---- eligible 为真且 intervention_enabled 为真（第 70 行）
    |          |
    |          v
    |     normalized=True，返回 None（第 71–72 行）
    |
    +---- 其余情况（第 73 行）
               |
               v
          继续抛出原异常
```

结合三次记录（**代码推断**，结论均须受「问题一」第 4 小节的缺口约束）：

- **K7：最终抛出 `PermissionError`。** 说明第 70 行的两个条件没有同时成立，走第 73 行重新抛出；异常栈显示抛出点在代理第 501 行，经过包装第 55 行与探针第 128 行，与"未执行归一化"的路径相符。
- **R3：最终正常返回。** 在宿主 `ok=false` 的情况下，Python 层仍能正常返回只有一条路径：第 70–72 行的归一化分支（`return None`）。另一条返回 `None` 的路径是代理第 757 行，但它要求 `_broker_request_host_operation` 返回 `True`，即宿主 `ok=true`，与 R3 记录的 `response_ok=false` 矛盾；返回 `False` 的情形（第 495、499 行）则会落到原生回退，与记录的请求已发出且只有一次请求不符。因此 R3 的正常返回与"启用异常归一化"的逻辑相符。
- **M8：最终抛出 `PermissionError`。** 同 K7。

两条原样保留的事实（**直接观察**）：

- 三次调用中，`exist_ok=True` 虽存在于 Python 原始调用中，但宿主请求中没有该字段，三次 `request_has_exist_ok` 均为 `false`。这意味着 Python 层的 `exist_ok` 语义并没有通过该请求字段传递给宿主操作；宿主是否另有等价处理，材料未提供。
- **R3 的正常返回不意味着宿主 mkdir 成功**：其宿主响应仍为 `ok=false`、`error=EEXIST`，目录状态也未改变。

#### 5. 本问的证据等级

| 环节                                                                | 等级     | 依据                                                                 |
| ------------------------------------------------------------------- | -------- | -------------------------------------------------------------------- |
| 三次调用的 Python 层返回结果（异常类型、异常文本、是否正常返回）    | 直接观察 | 02 各次调用记录                                                      |
| 三次调用的宿主响应字段（`ok`、`decision`、`error`）与策略字段 | 直接观察 | 02 `matching_responses`；日志 2346–2351／2365–2370／2372–2377   |
| K7、M8 的调用链（探针 128 → 包装 55 → 代理 754 → 代理 501）      | 直接观察 | 两次异常栈逐帧记录了经过的函数与行号                                 |
| R3 的调用链                                                         | 代码推断 | R3 无异常栈；由绑定检查、`matching_responses` 与代码路径排除法确定 |
| 代理第 501 行抛出后不回退到原生调用                                 | 代码推断 | 03 第 754–760 行                                                    |
| R3 正常返回对应 `eligible` 与 `intervention_enabled` 同时为真   | 代码推断 | 第 70–72 行是唯一可行路径；但二者实际值未记录                       |
| 各阶段的 `normalize` 配置、`eligible`／`normalized` 实际值    | 证据不足 | `PHASES` 定义与完整事件未提供                                      |

### 问题三：这些材料支持什么范围的判断？

#### A. 证据支持

1. **三次调用参数一致**：`exist_ok=true`、`mode=511`、`parents=false`（2.2 节；三次记录逐字段相同）。
2. **三次调用目标目录状态一致**：调用前均为已存在的空目录、非符号链接；调用前后 inode、device、uid、gid、mode、mtime_ns、ctime_ns、entries、is_directory、is_symlink 全部一致，且与初态一致（2.5 节）。
3. **三次宿主策略判断均为授权**：`matchedRuleSource=user-allowlist`、`decision=grant-token`、`result=approved`、`approvedByUser=false`（2348／2367／2374）。
4. **三次宿主实际操作均失败**：`operation=mkdir`、`ok=false`、`error=EEXIST`（2349／2368／2375）。
5. **R3 的 Python 正常返回不等于宿主成功**：R3 的 `response_ok=false`、`error=EEXIST`，目录状态未变（2.3、2.5 节）。
6. **临时包装的异常处理逻辑能解释 Python 层的差异**：K7／M8 走"重新抛出"，R3 走"满足条件且启用归一化时返回 `None`"（问题二第 2、4 小节）。
7. **Python 层的正常返回不能直接证明宿主文件操作成功**：本包提供的直接反例即 R3。

#### B. 不能据此得出的结论

1. **不能认为 R3 成功创建了目录**：宿主返回 `ok=false`、`EEXIST`，且目录状态（含 inode 与 `entries=[]`）在调用前后未变。
2. **不能认为 `approvedByUser=false` 代表宿主拒绝操作**：同一行日志同时记录 `decision=grant-token`、`result=approved`、`matchedRuleSource=user-allowlist`，`approvedByUser=false` 只表示本次未走逐次人工确认。
3. **不能直接认为 `PermissionError` 就是底层文件系统权限不足**：该异常由 WorkBuddy Python 代理在 `_broker_request_host_operation` 中主动抛出（第 501–504 行），触发条件是"有响应、`ok` 不为真、`decision` 不是 `sandbox`"；本次异常文本直接取自宿主响应中的 `EEXIST`。
4. **不能认为目标目录发生了变化**：三次调用前后十个状态字段逐一相同（2.5 节）；`EEXIST` 只说明创建目标已存在。
5. **不能将本次结果直接推广为所有 WorkBuddy 环境下的原生行为**：本次 Python 层结果经过了评测者的临时包装（`Path.mkdir` 被替换为 `scoped_mkdir`，`_broker_send_request` 被替换为 `observe_response`，第 78–82 行），且归一化本身由该包装实现。
6. **不能声称已经完全核实三次实验的阶段开关配置**：`PHASES` 未提供，`normalize` 值、`eligible`／`normalized` 实际状态均未记录。

#### C. 证据不足及后续需要补充的材料

| 待核实事项                                                                                                            | 影响的判断                                                     | 建议补充的材料                                    | 如果无法取得                         |
| --------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------ |
| `PHASES` 完整定义                                                                                                   | 无法直接核实各阶段配置（阶段名称、顺序、数量）                 | `probe_mkdir.py` 中 `PHASES` 的定义           | 保留为代码推断                       |
| 各阶段 `normalize` 值                                                                                               | 无法直接核实每次调用是否开启归一化                             | 每次调用的配置记录或阶段表                        | 不确认具体开关值                     |
| `eligible`、`normalized`、`intervention_enabled` 等内部状态                                                     | 无法直接确认异常归一化条件在每次调用中是否实际满足             | 包装函数的完整事件记录（`intervention.events`） | 说明现有材料不足，只按代码结构推断   |
| 宿主端实际处理实现                                                                                                    | 无法核实宿主内部如何执行 `mkdir`、是否接受 `exist_ok` 语义 | 宿主操作处理代码及完整日志                        | 只依据已记录的响应判断               |
| 无临时包装函数的对照实验                                                                                              | 无法推广为未干预环境的原生行为                                 | 原生调用对照实验                                  | 不作普遍性结论                       |
| 宿主日志节选连续性（第 2352–2364 行、第 2371 行未提供；连接标识 `PUBLIC_PID-106`→`PUBLIC_PID-110`→`PUBLIC_PID-111` 不连续） | 无法断言三次调用之间不存在其它宿主活动                         | 完整宿主日志                                      | 记录为节选，不作"仅有三次操作"的断言 |
| 每次调用记录中 `mkdir_attempts` 的统计口径                                                                          | 影响"每次调用仅尝试一次"的确认程度                             | `probe_mkdir.py` 第 140–182 行的记录装配代码   | 按归档原值引用，不解释为累计值       |

以上缺项在补充材料到位前不作为结论使用；本次未因材料缺失而补齐任何事实。

#### 小结

本包材料支持的最重要结论是：**在三次输入参数一致、目标目录状态一致的前提下，宿主策略三次都授权了操作（`grant-token`／`approved`），而宿主实际 `mkdir` 三次都以 `EEXIST` 失败（`ok=false`）；Python 层的差异（K7、M8 抛 `PermissionError`，R3 正常返回）来自评测者临时包装的异常归一化开关，而不是宿主策略判断或实际文件操作出现了差异。** 因此，Python 层正常返回既不能证明宿主文件操作成功，也不能证明目录状态发生了改变；`PermissionError` 也不能直接解读为操作系统权限不足。至于三次调用分别配置了什么开关，现有材料不足，仅能保留为代码推断。
