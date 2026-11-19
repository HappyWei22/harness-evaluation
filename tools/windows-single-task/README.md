# Windows 下的 DeepSeek Harness 单题运行

这套工具在 Windows 宿主机上控制 Hyper-V（Windows 的虚拟机管理功能）。它恢复来宾虚拟机快照，准备独立工作区，检查来宾能否打开宿主的评分侧文件，通过桌面界面发送一道题，再凭 DeepSeek Harness（DSH）的原生会话确认接收与结束。宿主取回结果并核验恢复后，才关闭尝试记录。

[11 月 19 日的运行证据](../../evidence/windows-single-task-2026-11-19/README.md)包含「复制一行」的正式通过、受阻请求、诊断和桌面预检。正式通过使用 v2 快照，恢复后由人工复核原件并补关账。当前入口默认选择 v3。现有 v3 实机证据覆盖启动到输入框就绪。

## 入口

| 文件 | 用途 |
| --- | --- |
| [run_dsh_task.py](run_dsh_task.py) | `run` 建立请求并运行，`status` 查看原请求，`resume` 从原记录继续收尾。 |
| [preflight_desktop.py](preflight_desktop.py) | 核对虚拟机、控制台桌面和 DSH 输入框，产品提交预算为零。 |
| [register_guest_credential.py](register_guest_credential.py) | 在宿主当前 Windows 用户下登记来宾凭据并验证连接。 |
| [inspect_task_native.py](inspect_task_native.py) | 根据完整题面、工作区和原生轮次事件，核对一次接收和同轮结束。 |
| [selftest_task_run.py](selftest_task_run.py) | 用临时账本和构造样本检查题包冻结、许可、轮次、产物和恢复判断。 |

脚本保留测试机的虚拟机名称、快照 ID、来宾测试账号名和控制目录默认值。新设备需先核对这些值及脚本中的来宾路径。宿主需要 Hyper-V 管理权限、Windows PowerShell、Python 3.10 或更新版本，以及能解压 Zstandard（原生日志采用的压缩格式）的 Node.js。来宾需要安装 DSH，并准备带 Windows 无障碍界面读取能力的桌面入口。

在仓库根目录可以先执行这两条离线核对命令。它们只读公开材料或使用临时自测目录。

~~~powershell
python -B tools/windows-single-task/selftest_task_run.py
python -B evidence/windows-single-task-2026-11-19/verify.py
~~~

下面是具备项目测试环境时的正式入口示例。它会尝试恢复 v2 并实际发送一道题。每次实机运行需使用新的请求 ID。

~~~powershell
python -B tools/windows-single-task/run_dsh_task.py run --baseline v2 --task tools/windows-single-task/tasks/copy-line-v1/task.json --request-id demo-v2-001
~~~

[修订与验收记录](versions/README.md)列出对应的修前、修后代码，对照样本与历史复算范围。
