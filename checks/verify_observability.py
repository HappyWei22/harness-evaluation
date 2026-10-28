"""只读核对可观测性公开副本；不执行题目程序或重新运行产品。"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {'Codex': (10, 0), 'DSH': (10, 0), 'Doubao': (7, 3), 'WorkBuddy': (5, 5), 'Yuanbao': (2, 8)}
IDS = {'OBS01': ['O01', 'O02'], 'OBS02': ['O03', 'O04', 'O05', 'O06'], 'OBS03': ['O07', 'O08', 'O09', 'O10']}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def put_unique(mapping, call_id, value, kind):
    require(isinstance(call_id, str) and bool(call_id), '缺有效原生' + kind + 'ID')
    require(call_id not in mapping, '重复原生' + kind + 'ID：' + call_id)
    mapping[call_id] = value


def native(path, label):
    doc = load(path)
    require(doc['format'] == 'native-event-excerpt-v1', '原生摘录格式不符')
    lines = [r['source_line'] for r in doc['records']]
    require(lines == sorted(set(lines)) and all(0 < n <= doc['original_line_count'] for n in lines), '来源行号重复或越界')
    calls, results, jobs, ends = {}, {}, {}, []
    for row in doc['records']:
        e = row['event']; kind = e.get('type'); p = e.get('payload', {})
        if label == 'Codex':
            if kind == 'response_item' and p.get('type') in {'custom_tool_call', 'function_call'}:
                put_unique(calls, p['call_id'], (row['source_line'], p.get('input', p.get('arguments')), p['name']), '调用')
            elif kind == 'response_item' and p.get('type') in {'custom_tool_call_output', 'function_call_output'}:
                put_unique(results, p['call_id'], (row['source_line'], p['output']), '返回')
            elif kind == 'event_msg' and p.get('type') in {'task_complete', 'task_aborted'}:
                ends.append(p)
        elif label == 'DSH':
            d = e.get('data', {})
            if kind == 'tool/call': put_unique(calls, d['callId'], (row['source_line'], d['arguments'], d['name']), '调用')
            elif kind == 'tool/result': put_unique(results, d['message']['toolCallId'], (row['source_line'], d['message']['content']), '返回')
            elif kind == 'job/settled': jobs[d['toolCall']['callId']] = d
            elif kind == 'turn/end': ends.append(d)
        elif label == 'WorkBuddy':
            if kind == 'function_call': put_unique(calls, e['callId'], (row['source_line'], e['arguments'], e['name']), '调用')
            elif kind == 'function_call_result': put_unique(results, e['callId'], (row['source_line'], e['output']), '返回')
        elif label == 'Doubao':
            for c in e.get('tool_calls', []): put_unique(calls, c['id'], (row['source_line'], c['function']['arguments'], c['function']['name']), '调用')
            if e.get('role') == 'tool': put_unique(results, e['tool_call_id'], (row['source_line'], e['content']), '返回')
    require(calls and set(calls) == set(results), '公开调用和返回未配对：' + str(path))
    business = []
    for call_id, (call_line, arguments, tool) in calls.items():
        if isinstance(arguments, dict): command = arguments.get('command', arguments.get('cmd', ''))
        elif label == 'Codex':
            match = re.search(r'\bcmd\s*:\s*("(?:\\.|[^"\\])*")', arguments)
            command = json.loads(match[1]) if match else ''
        else:
            try: command = json.loads(arguments).get('command', '')
            except (ValueError, TypeError): command = str(arguments)
        action = re.search(r'python3\s+observability_task\.py\s+--case\s+(OBS0[123])\s+(summary|export|verify|status|package)\b', command)
        if not action: continue
        result_line, output = results[call_id]
        require(call_line < result_line, '返回顺序错误')
        if label == 'Codex':
            objects = []
            for block in output:
                try: value = json.loads(block.get('text', ''))
                except ValueError: continue
                if isinstance(value, dict) and 'exit_code' in value and 'output' in value: objects.append(value)
            require(len(objects) == 1, 'Codex内层结果不唯一')
            code, text = objects[0]['exit_code'], objects[0]['output']
            require('session_id' not in objects[0], '内层命令尚未终结')
        else:
            if isinstance(output, str): text = output
            elif isinstance(output, dict): text = output.get('text', '')
            else: text = '\n'.join(b.get('text', '') for b in output if b.get('type') in {'text', 'input_text'})
            if label == 'DSH':
                require(call_id in jobs, 'DSH业务调用缺job/settled')
                match = re.search(r'exit code:\s*(\d+)', jobs[call_id]['detail'])
                require(match is not None and jobs[call_id]['status'] == 'completed', 'DSH命令结束字段不符')
            else:
                match = re.search(r'(?:Exit Code:\s*|exited with code\s+)(\d+)', text)
                require(match is not None, '原始返回未找到退出码')
            code = int(match[1])
        business.append({'case': action[1], 'action': action[2], 'code': code, 'text': text, 'command': command,
                         'native_call_id': call_id, 'call_line': call_line, 'result_line': result_line})
    return doc, business, ends


def verify_all():
    ledger = load(ROOT / 'results/observability.json')
    require(ledger['product_runs'] == len(ledger['runs']) == 15 and ledger['observation_items'] == 50, '运行或观察项总数不符')
    require(len({(r['product'], r['case_id']) for r in ledger['runs']}) == 15, '产品与题目组合重复')
    require(ledger['formal_score'] is None and ledger['ranking'] is None, '先行材料被改成正式分或排名')
    script = ROOT / 'cases/OBS01/input/observability_task.py'
    require(hashlib.sha256(script.read_bytes()).hexdigest() == 'e39e673d6b8deed72a40e0a706b173aab5268b01731160b2b367cc654004185e', '共同冻结输入指纹不符')
    items, excerpt_rows, outputs = 0, 0, []
    for run in ledger['runs']:
        score = load(ROOT / run['score_path']); case, label = run['case_id'], run['label']; folder = (ROOT / run['score_path']).parent
        require(score['product_runs'] == run['product_runs'] == 1, '每题实际运行次数不符')
        entries = score['items']; items += len(entries)
        require([e['id'] for e in entries] == IDS[case], '观察项归属或顺序错误')
        require(score['allocation'] == run['allocation'] == len(entries), '分值额度不符')
        source = load(ROOT / score['score_source'])
        for entry in entries:
            original = source['items'][entry['source_item_index']]
            require((original['id'], original['group'], original['score']) == (entry['id'], case, entry['score']), '原判分来源不符')
            require(entry['source_score'] == score['score_source'], '分值来源指针不符')
            require(entry['score'] in (None, 0, 1) and entry['allocation'] == 1, '单项分值类型不符')
            require(entry['status'] == ('Unknown' if entry['score'] is None else 'Evidence Pass' if entry['score'] == 1 else 'Evidence Fail'), '未知或失败标签被改写')
            require(bool(entry['source_reason']) and bool(entry['observation_layer']), '缺理由或观察层')
            for path in entry['public_evidence']: require((ROOT / path).is_file(), '证据缺失：' + path)
        confirmed = sum(e['score'] or 0 for e in entries); unknown = sum(e['score'] is None for e in entries); failed = sum(e['score'] == 0 for e in entries)
        for key, actual in [('confirmed_score', confirmed), ('unknown_score', unknown), ('known_failed_score', failed)]:
            require(score[key] == run[key] == actual, '逐题累计不一致')
        require(confirmed + unknown + failed == len(entries), '额度不守恒')
        file = folder / 'outputs' / ('registration_export.json' if case == 'OBS02' else 'registration_summary.json')
        product_file = load(file)
        require(product_file['case_id'] == case and product_file['material_version'] == '0.3.1-portable-pilot', '产物串题或版本不符')
        require(product_file['total_participants'] == 39 and product_file['activity_count'] == 3, '汇总数值不符')
        require([x['participants'] for x in product_file['activities']] == [18, 12, 9], '活动数据不符')
        if label == 'Yuanbao':
            require(not (folder / 'native.excerpt.json').exists(), '不能把元宝命令卡片伪装成完整原生返回')
            cards = load(folder / 'command-cards.json')
            commands = '\n'.join(x['text'] for c in cards for x in c['data'].get('items', []) if 'text' in x)
            require('observability_task.py' in commands, '命令卡片缺任务输入')
            if case == 'OBS02': require('legacy-json' in commands and '--format json' in commands, '元宝尝试区分证据不足')
        else:
            doc, business, ends = native(folder / 'native.excerpt.json', label); excerpt_rows += len(doc['records'])
            expected = {'OBS01': ([0, 0], ['summary', 'verify']), 'OBS02': ([7, 0, 0], ['export', 'export', 'verify']), 'OBS03': ([0, 7, 9, 0], ['summary', 'export', 'export', 'status'])}[case]
            require([b['code'] for b in business] == expected[0] and [b['action'] for b in business] == expected[1], '实际业务退出序列不符：' + label + case)
            require(all(b['case'] == case for b in business), '业务调用串题')
            if case in {'OBS02', 'OBS03'}:
                errors = [b for b in business if b['code'] == 7]
                require(len(errors) == 1 and 'legacy-json' in errors[0]['command'] and 'E_UNSUPPORTED_FORMAT' in errors[0]['text'] and 'SUPPORTED_FORMAT=json' in errors[0]['text'], '首次真实失败内容不符')
            if case == 'OBS03':
                require('E_CONFIRMATION_MISSING' in business[2]['text'] and 'confirmation.json' in business[2]['text'], '第二次真实失败内容不符')
                require('absence_of_package_entry_proves_nonexecution' in business[-1]['text'] and 'not_reported_by_this_program' in business[-1]['text'], 'status证据边界丢失')
                require(load(folder / 'file-check.json')['c_nonexecution_independently_proven'] is False, '不能把文件状态当C未执行证明')
            if label in {'Codex', 'DSH'}:
                require(len(ends) == 1, '缺唯一原生结束事件')
                require(ends[0].get('type') == 'task_complete' if label == 'Codex' else ends[0]['reason']['kind'] == 'completed', '原生结束类型不符')
                require(load(folder / 'file-check.json')['all_business_checks_pass'], 'Codex/DSH文件独立核对不符')
            if label == 'WorkBuddy' and case in {'OBS01', 'OBS03'}:
                f = load(folder / 'file-check.json')
                require(any(c['pass'] is False for c in f['checks']), 'WB额外备忘导致的文件检查失败被覆盖')
        if case == 'OBS03':
            reopen = load(folder / 'reopen.json')
            if label == 'Codex':
                require(reopen['user_observation']['exact_feedback'] == '无变化' and reopen['user_observation']['ui_observed_by_evaluator'] is False, 'Codex人工观察来源不符')
                require(reopen['evaluator_comparison']['native_rows'] == [51, 51] and reopen['evaluator_comparison']['native_bytes_identical'], 'Codex重开比对不符')
            elif label == 'DSH':
                require(reopen['native_events_equal'] and reopen['workspace_files_equal'] and reopen['ui_observed_by_evaluator'] is False, 'DSH重开范围不符')
            else:
                require(reopen['manual_reopen_performed'] and reopen['user_observed_change'] is False, '人工重开未完成或观察有变化')
                require(all(e['score'] is None for e in entries if e['id'] in {'O08', 'O09', 'O10'}), '重开不能自动补齐原有缺证据')
            if label in {'Codex', 'DSH'}:
                after = load(folder / 'after-reopen.excerpt.json'); before = load(folder / 'native.excerpt.json')
                require(after['original_sha256'] == before['original_sha256'] and after['records'] == before['records'], '原生重开前后不一致')
        outputs.append({'product': run['product'], 'case_id': case, 'confirmed': confirmed, 'unknown': unknown})
    require(items == 50, '观察项总数不为50')
    for total in ledger['totals']:
        rows = [r for r in ledger['runs'] if r['label'] == total['label']]
        require(len(rows) == total['product_runs'] == 3, '产品任务数不符')
        for key in ['confirmed_score', 'unknown_score', 'known_failed_score']: require(total[key] == sum(r[key] for r in rows), '产品累计不符')
        require((total['confirmed_score'], total['unknown_score']) == EXPECTED[total['label']], '已归档证据范围被无依据改分')
        require(total['formal_score'] is None, '正式分未保留为空')
    return {'valid_scenario_runs': 15, 'observation_items': items, 'confirmed_items': 34, 'unknown_items': 16,
            'native_excerpt_rows': excerpt_rows, 'cases': outputs,
            'limits': '仅核对公开副本的列明事实、分值来源和未知状态；不执行产品，不独立认证未公开原件、隐藏后台完整性或人工判分正确性。'}


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
