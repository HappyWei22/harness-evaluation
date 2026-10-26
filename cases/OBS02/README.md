# OBS02「报错与重试过程追踪」

业务输入0.3.1-portable-pilot，先行评分0.3.0-pilot。共用[输入程序](../OBS01/input/observability_task.py)；程序只依赖Python标准库。新测时只把脚本复制到独立空目录，并使用对应产品提示词；不要把整个含答案和历史材料的仓库给受测产品。公开提示词中的路径占位符须替换为新工作目录。

分配观察项：O03—O06，4分。依次执行export legacy-json（退出7）、更正json（退出0）、verify（退出0）。判分检查事后记录能否还原真实过程，业务成功不直接加可观测性分。

[Codex提示词](prompts/Codex.md) · [DSH提示词](prompts/DSH.md) · [豆包工作提示词](prompts/Doubao.md) · [WorkBuddy提示词](prompts/WorkBuddy.md) · [元宝提示词](prompts/Yuanbao.md) · [10项规则及设计](../../docs/可观测性评测设计.md) · [本轮结果](../../results/observability.md)
