"""检查公开包Markdown本地链接、常见凭据格式及未处理的本机路径；仅静态读取。"""
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def check():
    broken, sensitive = [], []
    link_count = 0
    for path in ROOT.rglob('*.md'):
        text = path.read_text(encoding='utf-8')
        for match in re.finditer(r'\[[^\]\n]*\]\(([^\n)]+)\)', text):
            target = match.group(1).strip().strip('<>')
            if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target):
                continue
            raw, _, anchor = target.partition('#')
            dest = (path.parent / unquote(raw)).resolve() if raw else path
            link_count += 1
            if not dest.exists():
                broken.append({'file': str(path.relative_to(ROOT)), 'target': target})
            elif anchor and dest.suffix == '.md':
                body = dest.read_text(encoding='utf-8')
                ids = re.findall(r'<a id="([^"]+)"', body)
                titles = re.findall(r'^#+\s+(.+)$', body, re.M)
                slugs = [re.sub(r'[^\w\-\u4e00-\u9fff ]', '', s.lower()).replace(' ', '-') for s in titles]
                if anchor not in ids + slugs:
                    broken.append({'file': str(path.relative_to(ROOT)), 'target': target, 'reason': '锚点缺失'})
    patterns = {
        '未替换的macOS用户路径': r'/' + r'Users/[^/\s"\\]+/',
        '未替换的macOS临时路径': r'/(?:private/)?var/folders/[^/]+/',
        '常见API密钥格式': r'\bsk-[A-Za-z0-9_-]{20,}',
        '私钥头': r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
        '带用户名密码的URL': r'https?://[^\s/:]+:[^\s/@]+@',
        '邮箱地址': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
    }
    for path in ROOT.rglob('*'):
        if not path.is_file() or path.suffix not in {'.md', '.txt', '.json', '.log', '.py'}:
            continue
        text = path.read_text(encoding='utf-8')
        for name, pattern in patterns.items():
            if re.search(pattern, text):
                sensitive.append({'file': str(path.relative_to(ROOT)), 'kind': name})
        if path.suffix == '.json':
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                # Upstream commented configs and the intentionally invalid-package test fixture.
                if path.relative_to(ROOT).as_posix() in {
                    'tools/dsh-context-export/.oxlintrc.json',
                    'tools/dsh-context-export/tsconfig.json',
                    'tools/dsh-context-export/tsconfig.tests.json',
                    'tools/dsh-context-export/tests/host/fixtures/bad-pkg/package.json',
                }:
                    continue
                sensitive.append({'file': str(path.relative_to(ROOT)), 'kind': 'JSON语法不符'})
                continue
            def inspect(node):
                if isinstance(node, list):
                    for child in node: inspect(child)
                elif isinstance(node, dict):
                    if node.get('type') in {'reasoning', 'thinking', 'reasoning_text'}:
                        sensitive.append({'file': str(path.relative_to(ROOT)), 'kind': '未移除的推理内容块'})
                    for key, child in node.items():
                        if key in {'reasoning', 'reasoning_content', 'replayState', 'access_token', 'api_key', 'password'}:
                            sensitive.append({'file': str(path.relative_to(ROOT)), 'kind': '需检查的内容字段：' + key})
                        inspect(child)
            inspect(value)
    report = {'checked_markdown_links': link_count, 'broken_links': broken,
              'static_pattern_findings': sensitive,
              'scope': '只检查本地链接/锚点和列出的格式，不访问外部网页，不保证穷尽所有隐私或授权问题。'}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if broken or sensitive else 0


if __name__ == '__main__':
    sys.exit(check())
