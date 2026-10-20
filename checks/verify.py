"""只读复核公开包；不运行探测程序、模型或受测产品，不访问网络。"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
FB = ROOT / 'evidence/维度评测/01-信息回传'
CASES = {'FB01': 'FB01-基础回传', 'FB02': 'FB02-较长结果回传', 'FB03': 'FB03-完整结果获取'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def excerpt(path):
    data = load(path)
    require(data['format'] == 'native-event-excerpt-v1', '摘录格式不符')
    numbers = [r['source_line'] for r in data['records']]
    require(numbers == sorted(set(numbers)), '原始行号重复或乱序')
    require(all(1 <= n <= data['original_line_count'] for n in numbers), '原始行号越界')
    return {r['source_line']: r['event'] for r in data['records']}


def text_of(event):
    if event.get('type') == 'tool/result':
        content = event['data']['message']['content']
    elif 'payload' in event:
        content = event['payload']['output']
        if isinstance(content, str):
            return content
    else:
        output = event['output']
        return output.get('text', '') if isinstance(output, dict) else output
    return '\n'.join(x.get('text', '') for x in content if x.get('type') in {'text', 'input_text', 'output_text'})


def pairs(rows):
    calls, results = {}, {}
    for line, event in rows.items():
        kind, p = event.get('type'), event.get('payload', {})
        if kind == 'tool/call':
            d = event['data']; calls[d['callId']] = (line, event, d['arguments'], d['name'])
        elif kind == 'tool/result':
            results[event['data']['message']['toolCallId']] = (line, event)
        elif kind == 'function_call':
            calls[event['callId']] = (line, event, event['arguments'], event['name'])
        elif kind == 'function_call_result':
            results[event['callId']] = (line, event)
        elif kind == 'response_item' and p.get('type') in {'custom_tool_call', 'function_call'}:
            calls[p['call_id']] = (line, event, p.get('input', p.get('arguments')), p['name'])
        elif kind == 'response_item' and p.get('type') in {'custom_tool_call_output', 'function_call_output'}:
            results[p['call_id']] = (line, event)
    require(set(calls) == set(results), '调用/结果没有完整配对')
    return [{'call_id': k, 'line': v[0], 'call': v[1], 'arguments': v[2], 'tool': v[3],
             'result_line': results[k][0], 'result': results[k][1]} for k, v in calls.items()]


def command(pair):
    arg = pair['arguments']
    if isinstance(arg, dict):
        return arg.get('command', '')
    try:
        obj = json.loads(arg)
        return obj.get('command', obj.get('cmd', ''))
    except (ValueError, TypeError):
        return arg


def codex_result(pair):
    for block in pair['result']['payload']['output']:
        try:
            value = json.loads(block.get('text', ''))
        except ValueError:
            continue
        if isinstance(value, dict) and 'exit_code' in value and 'output' in value:
            return value
    raise ValueError('未找到Codex底层exec_command结果')


def receipts(run):
    return list((run / '原始材料/回执').glob('*.json')) + list((run / '原始材料').glob('FB0*.json'))


def one_receipt(run):
    found = receipts(run)
    require(len(found) == 1, '应只有一份本轮回执：' + str(run))
    return load(found[0])


def native_file(run, name=None):
    if name:
        return run / '原始材料' / name
    found = list((run / '原始材料').glob('*.excerpt.json'))
    require(len(found) == 1, '需要明确本轮摘录：' + str(run))
    return found[0]


def verify_receipt(receipt, case):
    name = {'FB01': 'feedback_probe.py', 'FB02': 'long_feedback_probe.py', 'FB03': 'full_output_probe.py'}[case]
    require(digest((ROOT / 'cases' / case / 'input' / name).read_bytes()) == receipt['script_sha256'], '输入程序与历史回执指纹不符')
    if 'expected_stdout_sha256' in receipt:
        require(digest(receipt['expected_stdout'].encode('ascii')) == receipt['expected_stdout_sha256'], '回执报告指纹不符')


def verify_group(group):
    case, product = group.split(':')
    label = {'豆包办公': '豆包', '元宝专家模式': '元宝'}.get(product, product)
    label += '-a02' if case == 'FB01' and product in {'WorkBuddy', '元宝专家模式'} else '-a01'
    run = FB / CASES[case] / '运行记录' / label
    if product == '元宝专家模式' or (product == '豆包办公' and case != 'FB03'):
        return '只核对分值来源及缺证状态；产品表现未获得充分验证。'
    if case in {'FB01', 'FB02'}:
        pp = pairs(excerpt(native_file(run)))
        if case == 'FB01':
            rr = {d['mode']: d for d in map(load, receipts(run))}
            for d in rr.values():
                verify_receipt(d, case)
            for mode in ['normal', 'error']:
                matching = [p for p in pp if 'feedback_probe.py ' + mode in command(p)]
                require(len(matching) == 1, '预设命令未唯一配对：' + group + ':' + mode)
                pair = matching[0]; actual = text_of(pair['result'])
                require('echo' not in command(pair), '本轮状态被echo包装')
                if product == 'WorkBuddy' and mode == 'error':
                    require('FIXTURE_SETUP_ERROR: cannot save fixture receipt' in actual, '缺保存回执失败现场')
                    require(pair['result']['providerData']['toolResult']['rawResponse']['exitCode'] == 20, '提前退出状态不符')
                    require('error' not in rr, '不应有有效预设错误回执')
                    continue
                d = rr[mode]
                if product == 'Codex':
                    v = codex_result(pair)
                    require(v['exit_code'] == d['expected_exit_code'], '退出码不符')
                    require(v['output'] == d['expected_stdout'] + d['expected_stderr'], '首次字段不符')
                else:
                    require((d['expected_stdout'] + d['expected_stderr']) in actual.replace('\r\n', '\n'), '字段不符')
                    if product == 'DSH':
                        require(pair['result']['data']['message']['isError'] is False, '状态解释条件变化')
                        require(('[exit code: 7]' in actual) if mode == 'error' else ('exit code' not in actual.lower()), 'DSH状态证据不符')
                    else:
                        require(pair['result']['providerData']['toolResult']['rawResponse']['exitCode'] == 0, '正常状态不符')
            return '正常/错误事件与回执已核对；DSH正常状态仍未知，WorkBuddy错误项仍未有效测试。'
        d = one_receipt(run); verify_receipt(d, case)
        target = [p for p in pp if 'python3 long_feedback_probe.py' in command(p)]
        require(len(target) == 1, '报告生成次数不符')
        pair = target[0]; actual = text_of(pair['result'])
        if product == 'Codex':
            v = codex_result(pair); actual = v['output']
            require(v['exit_code'] == 0, '正常状态不符')
            require('max_output_tokens' not in command(pair), '首次额度被人为调整')
        elif product == 'WorkBuddy':
            footer = '\nStderr: (empty)\nExit Code: 0\nSignal: (none)'
            require(actual.endswith(footer), '工具结果格式变化')
            require('Output too large' in pair['result']['providerData']['toolResult']['content'], '缺同条结果另一表示')
            actual = actual.split('\nStdout: ', 1)[1][:-len(footer)]
        normalized = actual.replace('\r\n', '\n')
        require(normalized == d['expected_stdout'], '200行正文与预期不符')
        require(len(normalized.encode()) == 17929 and len(normalized.splitlines()) == 200, '长度或行数不符')
        for ck in d['checkpoints']:
            require(normalized.splitlines()[ck['line'] - 1] == ck['text'], '指定字段不符')
        return '200行正文与四处字段可复核；不证明完整模型请求。'
    d = one_receipt(run); verify_receipt(d, case)
    expected = d['expected_stdout'].encode('ascii')
    require(len(expected) == 262144 and len(expected.splitlines()) == 2048, '报告规格不符')
    if product == '豆包办公':
        original = (run / '产品完整输出/入口源文件.txt').read_bytes()
        copy = (run / '产品完整输出/FB03-完整输出.txt').read_bytes()
        dom = (run / '补充核查证据/搜索结果展开-DOM.txt').read_text()
        tool = dom.split('- textbox:', 1)[1].split('- generic: 字段已提取', 1)[0]
        require(original == copy == expected, '豆包全文与副本不符')
        require(all(v in tool for v in d['markers'].values()), '工具卡片缺标记')
        require(':1024:RUN_ID=' + d['run_id'] + ' MIDDLE_TOKEN=' + d['markers']['MIDDLE_TOKEN'] in tool, '中间行未核对')
        return '补充工具卡片及全文支持后两项；首次截断说明和入口仍未知。'
    names = {'Codex': '第一阶段原生会话.excerpt.json', 'WorkBuddy': '第二阶段后完整会话.excerpt.json', 'DSH': 'session-两阶段.v4.excerpt.json'}
    pp = pairs(excerpt(native_file(run, names[product])))
    generation = [p for p in pp if re.search(r'python3\s+(?:[^\n]*/)?full_output_probe\.py(?:[\"\s;]|$)', command(p))]
    require(len(generation) == 1, '本公开摘录中报告生成次数不为一次')
    first = generation[0]; actual = text_of(first['result'])
    if product == 'Codex':
        require('Warning: truncated output (original token count: 66075)' in actual, '缺首次截断提示')
        require('…56075 tokens truncated…' in actual, '截断位置不符')
        require('MIDDLE_TOKEN=' not in actual and d['markers']['TAIL_TOKEN'] in actual, '可见标记变化')
        require(not re.search(r'full output|saved to|stored at|/PUBLIC_|session_id', actual, re.I), '发现需重新审查的可能入口')
        require(actual == (run / '第一阶段实际返回.txt').read_bytes().decode('utf-8'), '首次返回与摘录不符')
        return '首次明确截断而无有效入口；同一事件影响两项，底层全文保存未知。'
    if product == 'WorkBuddy':
        require('Preview (first 2048 bytes)' in actual, '缺预览提示')
        require('MIDDLE_TOKEN=' not in actual and 'TAIL_TOKEN=' not in actual, '首次字段条件变化')
        raw = (run / '原始材料/完整入口文件-第二阶段取证.txt').read_bytes()
        initial = (run / '原始材料/完整入口文件-第一阶段取证.txt').read_bytes()
        require(raw == initial, '前后入口副本不符')
        require(len(raw) == 264202 and raw.count(b'\r') == 2058 and raw.count(b'\n') == 2048, '额外回车或行数不符')
        require(raw != expected and raw.replace(b'\r', b'') == expected, '字节差异不能仅由回车解释')
        later = '\n'.join(text_of(p['result']) for p in pp if p['line'] > first['line'])
        require(all(v in later for v in d['markers'].values()), '实际后续返回缺标记')
        require(digest(raw) in later, '后续结果缺完整文件指纹')
        path = re.search(r'Full output saved to: ([^\n]+)', actual)
        require(path is not None, '未保留首次完整入口')
        require(any(path.group(1) in command(p) for p in pp if p['line'] > first['line']), '后续调用没有使用首次入口')
        return '前三项有现场；文件多2058个回车，严格字节保存项未通过，正文去回车后一致。'
    entry = re.search(r'\[output truncated; full output: ([^\]]+)\]', actual)
    require(entry is not None and 'Omitted 14462 bytes' in actual, '缺首次截断及完整入口')
    retrievals = [p for p in pp if p['tool'] == 'grep' and json.loads(p['arguments']).get('path') == entry.group(1)]
    retrieved = '\n'.join(text_of(p['result']) for p in retrievals)
    require(all(v in retrieved for v in d['markers'].values()), '未从原入口实际读回标记')
    raw = (run / '产品完整输出/FB03-原始stdout.log').read_bytes()
    require(raw == expected and digest(raw) == d['expected_stdout_sha256'], '全文未逐字节一致')
    later = '\n'.join(text_of(p['result']) for p in pp if p['line'] > first['line'])
    require(digest(raw) in later and 'COPIED to:' in later, '缺产品复制文件的工具证据')
    return '截断说明、有效入口、人工指定后的实际读取及全文一致性可复核。'


def pointer(document, location):
    for token in location.lstrip('/').split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        document = document[int(token)] if isinstance(document, list) else document[token]
    return document


def message_text(event, product):
    if product == 'DSH':
        d = event['data']; msg = d.get('message', d)
    else:
        msg = event.get('payload', event)
    content = msg['content']
    body = content if isinstance(content, str) else '\n'.join(x.get('text', '') for x in content if x.get('type') in {'text', 'input_text', 'output_text'})
    if product == 'WorkBuddy' and msg['role'] == 'user':
        body = re.search(r'<user_query>(.*?)</user_query>', body, re.S).group(1)
    return msg['role'], body.strip()


def normalize_prompt(body):
    body = re.sub(r'^```text\s*', '', body)
    body = re.sub(r'\s*```$', '', body)
    return re.sub(r'\s+', '', body)


def verify_context():
    expected = load(ROOT / 'cases/CTX01/evaluator/expected.json')
    names = ['第一轮-设定条件.md', '第二轮-更新要求.md', '第三轮-输出当前记录.md']
    verified = []
    for path in sorted((ROOT / 'results/CTX01').glob('*.json')):
        data = load(path); messages = data['messages']; label = data['label']
        require([m['role'] for m in messages] == ['user', 'assistant'] * 3, '三轮顺序不符')
        previous = 0
        for m in messages:
            require(min(m['source_lines']) > previous, '来源顺序不符')
            previous = max(m['source_lines']); source = ROOT / m['public_source']
            if source.name.endswith('.excerpt.json'):
                role, body = message_text(excerpt(source)[m['source_lines'][0]], label)
                require(role == m['role'] and body == m['text'], '原生消息与整理文本不符')
            else:
                raw = source.read_text(); parts = []
                for node in m['accessibility_node_ids']:
                    found = re.search(r'^\s*' + str(node) + r' (?:文本|text) (.*?)(?=\n\s*\d+ [^\n]+|\Z)', raw, re.M | re.S)
                    require(found is not None, '网页节点缺失')
                    parts.append(found.group(1).strip())
                require('\n'.join(parts) == m['text'], '网页节点与整理问答不符')
        for i, name in enumerate(names):
            prompt = (ROOT / 'cases/CTX01/prompts' / name).read_text()
            require(normalize_prompt(messages[2*i]['text']) == normalize_prompt(prompt), '提交条件不等价')
        require(not any(str(v) in messages[4]['text'] for v in expected.values()), '第三轮含预期答案')
        final = json.loads(re.search(r'\{.*\}', messages[-1]['text'], re.S).group())
        require(final == expected, '最终四字段不符')
        score = load(ROOT / data['source_score'])
        require(len(score['score_items']) == 4, '评分项数不符')
        for item in score['score_items']:
            require(item['points'] == item['max_points'] == 1 and item['actual'] == item['expected'], '历史字段评分不符')
        verified.append({'product': data['product'], 'points': '4/4', 'scope': '本轮短对话可见表现；不验证压缩或内部独立责任。'})
    require(len(verified) == 5, '五款上下文材料不齐')
    return verified


def check_manifest():
    listed = set()
    for item in load(ROOT / 'MANIFEST.json')['files']:
        rel = item['path']; target = ROOT / rel
        require(target.is_file() and not target.is_symlink(), '缺文件或为链接：' + rel)
        blob = target.read_bytes()
        require(len(blob) == item['bytes'] and digest(blob) == item['sha256'], '文件指纹变化：' + rel)
        listed.add(rel)
    actual = {str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts and p.name != '.DS_Store'}
    require(actual == listed | {'MANIFEST.json'}, '文件清单遗漏或多出文件')
    return len(listed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--item', help='只复核一项，如WB-FB03-04；仍检查全包指纹。')
    args = parser.parse_args(); files = check_manifest()
    ledger = load(ROOT / 'results/information-feedback.json'); all_rows = ledger['judgments']
    require(len(all_rows) == 60 and len({r['id'] for r in all_rows}) == 60, '判断总数不符')
    selected = [r for r in all_rows if not args.item or r['id'] == args.item]
    require(bool(selected), '未找到观察项：' + str(args.item))
    for total in ledger['review_totals']:
        rr = [r for r in all_rows if r['product'] == total['product']]
        supported = sum(r['historical_points'] or 0 for r in rr if r['review_state'] == 'supported_positive')
        pending = sum(r['historical_points'] or 0 for r in rr if r['review_state'] == 'positive_evidence_pending')
        require(len(rr) == 12, '每款判断数量不符')
        require(supported == total['archived_evidence_supported_positive_points'], '归档支持分值不符')
        require(pending == total['historical_positive_points_waiting_for_raw_evidence'], '待补现场分值不符')
        require(supported + pending == total['historical_confirmed_points'], '历史分值不符')
        require(total['historical_confirmed_points'] + total['confirmed_failed_points'] + total['unrated_points'] == 20, '分值额度不守恒')
    for r in selected:
        source = ROOT / r['source']
        if r['source_pointer']:
            require(pointer(load(source), r['source_pointer']) == r['historical_points'], '历史分值来源不符：' + r['id'])
        else:
            require('豆包目前确认3分' in source.read_text(), '缺历史阶段来源')
        for e in r['evidence']:
            require(bool(e['public_path']) == e['public_available'], '证据公开标记不一致')
            if e['public_available']:
                require((ROOT / e['public_path']).is_file(), '公开证据缺失')
            else:
                require(r['review_state'] == 'insufficient_evidence', '未公开证据影响已确认判断')
    groups = {g: verify_group(g) for g in sorted({r['verification_group'] for r in selected})}
    ctx = None if args.item else verify_context()
    recent = None
    if not args.item:
        from verify_recent import verify_all
        recent = verify_all()
    print(json.dumps({'scope': '公开副本核对，不重跑产品，不代表全部表现通过或内部根因已验证。',
                     'public_files_checked': files, 'feedback_judgments_checked': len(selected),
                     'feedback_states': dict(Counter(r['review_state'] for r in selected)),
                     'historical_positive_points_waiting_for_raw_evidence': sum(r['historical_points'] or 0 for r in selected if r['review_state'] == 'positive_evidence_pending'),
                     'groups': groups, 'context_products': ctx,
                     'recent_evidence': recent}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, IndexError, OSError, AttributeError) as exc:
        print('核对未通过：' + str(exc), file=sys.stderr)
        sys.exit(1)
