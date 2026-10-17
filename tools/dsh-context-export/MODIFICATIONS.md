# 本地扩展改动说明

基于dsh-context 0.72.0，提交ca89acfd93844d5bac2b82fdfce6d99c118e71a0。上游许可证及作者信息保留。

新增完整事件读取、JSON组装、导出按钮、原生日志服务连接及两组测试；在已有上下文页面和弹窗接入按钮，增加中英文文案、样式和客户端模块声明。源码修改处附有改动标记，具体差异见context-export.patch。

本仓库的安装脚本另外从UPSTREAM.json读取基线提交，避免把外层评测仓库的提交误记为上游版本；没有因此修改实际导出功能。未复制上游自动发布工作流、Git钩子、代理开发指令和演示图片；保留构建、运行和测试需要的源文件。

## 改动或新增文件

- `package.json`
- `src/client/components/browser.tsx`
- `src/client/components/contextModal.tsx`
- `src/client/components/contextView.tsx`
- `src/client/i18nEn.ts`
- `src/client/i18nZh.ts`
- `src/client/index.ts`
- `src/client/styles/browser.css`
- `src/client/contextExport.ts`
- `src/client/nativeLogExport.ts`
- `src/client/components/contextExport.tsx`
- `tests/client/contextExport.spec.ts`
- `tests/client/components/contextExport.spec.ts`
- `scripts/install-local-export.py`
