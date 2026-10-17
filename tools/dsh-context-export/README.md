# DSH上下文与会话日志导出扩展

本目录基于[bowenliang123/dsh-context](https://github.com/bowenliang123/dsh-context) 0.72.0增加两个导出按钮。上游基线提交为`ca89acfd93844d5bac2b82fdfce6d99c118e71a0`，本地扩展标识为`context-export-1`。原插件的上下文统计、浏览和管理能力归原项目；这里新增的是导出入口及相关读取、缺失标记和调用逻辑。

## 为什么增加导出

评测时，需要检查某一步之前已有的消息和工具结果。仅看面板里的缩略内容，不方便保存完整正文和事件位置。因此，在上下文浏览器增加导出按钮，把所选步骤的还原结果保存成文件，并提供原生日志下载入口，便于后续对照。

| 新增按钮 | 导出内容 | 范围 |
|---|---|---|
| 导出所选上下文JSON | 所选轮次、步骤及插件还原的消息，相关原始事件、序号和缺失标记 | 历史步骤回答前的状态；Live为下一次请求的候选状态 |
| 下载原始会话日志ZIP | 调用DSH已有的会话日志下载功能 | 整个会话，不受所选步骤限制 |

JSON是一种按字段保存信息的文本格式。导出会读取所列消息的完整事件；读不到的内容会记录缺失，不用缩略文字冒充完整正文。ZIP下载复用DSH原生功能，不是本项目重新实现了整套日志导出。

## 证据能说明什么

导出的JSON来自日志还原，`actualProviderRequestCaptured`固定为`false`。它有助于检查插件认为某步有哪些上下文，不等于抓到了实际发给模型服务的请求。原始日志与导出结果应对照使用，不能据导出文件自动确认服务端接收了所有消息。

历史桌面实测使用已有CTX01「多轮要求更新」第3轮，实际点击过两个按钮，没有重跑任务。本次对保存文件重新做只读核对：9条所选原始事件与40条会话记录中的对应事件一致，且不含第3轮回答。范围及未验证项目见[validation.json](validation.json)。该样本没有压缩、工具调用、子会话或附件，不能代表这些场景已经验收。

## 代码与改动

- [contextExport.ts](src/client/contextExport.ts)：读取完整事件、组装上下文JSON及缺失标记。
- [contextExport.tsx](src/client/components/contextExport.tsx)：两个按钮及导出状态。
- [nativeLogExport.ts](src/client/nativeLogExport.ts)：连接DSH原生日志下载服务。
- [改动清单](MODIFICATIONS.md)、[相对上游的补丁](context-export.patch)、[来源版本](UPSTREAM.json)。

本目录保留构建所需源码、配置和测试；不包含依赖目录、个人导出文件和本机安装备份。

## 构建与检查

先安装Node.js和pnpm，在本目录安装锁文件指定的依赖，再构建：

```bash
pnpm install --frozen-lockfile --ignore-scripts
pnpm run build
```

`--ignore-scripts`表示安装依赖时不运行包的生命周期脚本；构建在下一条命令显式执行。新增导出逻辑的测试入口：

```bash
pnpm exec vitest run tests/client/contextExport.spec.ts tests/client/components/contextExport.spec.ts
```

这检查程序逻辑，不能替代真实DSH界面的操作验证。当前源码声明的依赖和兼容信息保留上游版本；本地扩展的实际桌面验证仅覆盖DSH 0.2.0-rc.2。历史本地说明记录过类型、规范、构建及客户端测试通过，本次重新执行的范围在[构建与测试记录](BUILD_VALIDATION.json)中另列。

本地安装脚本见[scripts/install-local-export.py](scripts/install-local-export.py)。它面向已安装的dsh-context 0.72.0，只替换客户端构建文件及其模块声明，保存备份，并在恢复前核对文件指纹。构建本身不修改DSH安装；执行安装脚本才会修改指定安装位置。

## 来源与许可

保留上游[Apache-2.0许可证](LICENSE)及作者信息。[上游README快照](UPSTREAM_README.txt)描述原插件完整能力，本目录README只说明本项目的导出扩展。其余上游功能不能作为本项目从零实现的成果。包名和基础版本沿用上游用于本地兼容，扩展没有发布为上游或npm正式版本。
