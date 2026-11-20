# 判定逻辑修订留存

这些归档保留原字节。每个压缩包只包含该次修订涉及的代码，部分包也附有当时的自测脚本。

| 修订 | 修前与修后 | 对照与验收范围 |
| --- | --- | --- |
| 将接收和终态绑定到原生会话 | [修前](single-task-lifecycle.pre-native.zip) · [修后](single-task-lifecycle.native-turn.zip) | 正例为全文匹配的一次提交及同轮终态。重复题面、旧会话、其他工作区、跨轮终态、半写入日志均应拒绝。修改者使用 11 月 17 日「正常启动」历史原件复算，结果仍为一次提交、第 1 轮完成，原裁定保留。错误工作区复算无法建立绑定。[公开的历史结论](../../../evidence/controlled-startup-windows-2026-11-17/g01-normal-start.json) |
| 正式发送前的读取门禁 | [修前](single-task-isolation.before.zip) · [修后](single-task-isolation.after.zip) | 旧门禁用 DSH 读取工作区样本和处理宿主标记。授权样本可读而宿主读取返回原因不明的错误时，正式发送会被拦截。[实机对照](../../../evidence/windows-single-task-2026-11-19/legacy-gate-block/isolation-decision.json) |
| Windows 隔离预检和保留阻断现场 | [原读取门禁](single-task-zero-submit.pre-change.zip) · [零提交预检](single-task-zero-submit.post-change.zip) · [启动保护修正](single-task-zero-submit.live-guard.zip) | 通过例签发一张正式许可。文件可打开、文件不存在、错误未知、共享通道可达、远程桌面会话、启动事件变化或记录缺失均应拦截。修改者用 Windows 本地文件和相邻的不存在路径对照文件打开结果，以替身命令检查阻断后的收尾分支。随后分别留下实机阻断和实机放行，旧门禁的历史阻断照原裁定留存。[端口复核](../../../evidence/windows-single-task-2026-11-19/port-block/share-recheck.json) · [正式运行前预检](../../../evidence/windows-single-task-2026-11-19/formal-v2/isolation-preflight.json) |
| 同一事件编号下的恢复复核 | [修前](single-task-restore-recheck.before.zip) · [修后](single-task-restore-recheck.after.zip) | 正例为事件编号相同且启动时间更新。原开机未变化、错误来宾、会话残留均应拒绝。修改者另用本轮封存原件副本和模拟账本复算，一条原提交得到补关账，原始失败报告保留。更早阻断请求缺正式提交与题后恢复证据，原裁定保留。[初判](../../../evidence/windows-single-task-2026-11-19/formal-v2/restore-final.json) · [复核](../../../evidence/windows-single-task-2026-11-19/formal-v2/restore-recheck.json) |

42 项现行离线用例可通过 [自测入口](../selftest_task_run.py)复核。原件复算由修改者执行，独立于构造样本。公开证据核对脚本可供其他读者重复检查已有材料。
