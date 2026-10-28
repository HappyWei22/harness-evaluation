"""核对2026-10-27同标准复核版本的来源、变更和累计；不自动裁定人工语义。"""
import hashlib
import json
from pathlib import Path
from verify_observability import IDS, load, native, require

ROOT = Path(__file__).resolve().parents[1]
VERSION = '2026-10-27-r1'
FOLDER = 'evidence/observability-review/' + VERSION
DELTA = {('Doubao','O08'), ('Doubao','O09'), ('Doubao','O10'), ('WorkBuddy','O08')}


def verify_all():
    pointer = load(ROOT / 'results/observability-current.json')
    require(pointer['review_version'] == VERSION, '当前版本指针不符')
    current = load(ROOT / pointer['result'])
    old = load(ROOT / pointer['historical_result'])
    require(current['review_version'] == VERSION and current['historical_result'] == pointer['historical_result'], '历史版本关系不符')
    require(current['product_runs'] == old['product_runs'] == 15 and current['new_product_runs'] == 0, '复核误计新增运行')
    require(current['observation_items'] == 50 and current['formal_score'] is None and current['ranking'] is None, '覆盖或正式分不符')
    require(current['rule_version'] == old['rule_version'] and current['business_input_version'] == old['business_input_version'], '复核悄然换规则或输入')
    require(len(current['runs']) == 15 and len({(r['label'],r['case_id']) for r in current['runs']}) == 15, '运行归属或数量不符')
    policy = load(ROOT / current['review_policy'])
    require(policy['new_product_runs'] == 0 and policy['independent_human_reviewer'] is False, '复核范围混淆')
    old_runs = {(r['label'],r['case_id']): r for r in old['runs']}
    actual_changes = set()
    for run in current['runs']:
        label, case = run['label'], run['case_id']
        prior_run = old_runs[label,case]
        require(all(run[k] == prior_run[k] for k in ('product','case_id','attempt','product_runs','allocation')), '原运行身份改变')
        prior = load(ROOT / prior_run['score_path'])
        score = load(ROOT / run['score_path'])
        require([e['id'] for e in score['items']] == IDS[case], '观察项归属变化')
        if case != 'OBS03':
            require(run == prior_run and score == prior, '本次范围外改分')
        else:
            require(score['historical_score'] == prior_run['score_path'], '缺旧评分指针')
            audit = load(ROOT / FOLDER / label / 'audit.json')
            require(audit['case_id'] == case and audit['label'] == label and audit['new_product_runs'] == 0, '审阅归属不符')
            for item, old_item in zip(score['items'], prior['items']):
                require(item['historical_score'] == old_item['score'], '历史分值被回写')
                require(item['source_reason'] == old_item['source_reason'], '历史理由被覆盖')
                if item['score'] != old_item['score']:
                    actual_changes.add((label,item['id']))
                    require(old_item['score'] is None and item['score'] == 1, '非预期改分类型')
                if item['id'] != 'O07':
                    require(item['score'] == audit['scores'][item['id']] and item['review_reason'], '复核理由或分值不对应')
                for p in item['public_evidence']: require((ROOT / p).is_file(), '复核证据链接缺失：' + p)
                require(item['status'] == ('Unknown' if item['score'] is None else 'Evidence Pass' if item['score'] == 1 else 'Evidence Fail'), '分值状态不对应')
            require(audit['reopen']['manual_reopen_performed'] is True and audit['reopen']['user_statement_verbatim'], '丢失已做重开的事实')
            require(audit['reopen']['ui_observed_by_evaluator'] is False, '人工反馈冒充亲见')
            if label != 'Yuanbao':
                doc, events, ends = native(ROOT / FOLDER / label / 'all-requests.excerpt.json', label)
                o08 = audit['O08_review']
                require(doc['original_sha256'] == o08['native_sha256'] and doc['original_line_count'] == o08['native_rows'], '原件指纹或行数不符')
                require(len(audit['all_request_pairs']) == o08['native_pair_count'], '全请求审阅范围不符')
                expected_lines = {p[k] for p in audit['all_request_pairs'] for k in ('call_line', 'result_line')}
                require(expected_lines <= {r['source_line'] for r in doc['records']}, '公开摘录遗漏已列请求或返回')
                require(len({p['native_call_id'] for p in audit['all_request_pairs']}) == o08['native_pair_count'], '审阅请求编号重复')
                require([e['action'] for e in events] == ['summary','export','export','status'] and [e['code'] for e in events] == [0,7,9,0], '真实连续失败链不符')
                require(o08['all_execution_requests_terminal'] and not o08['unreviewed_execution_requests'], '存在未核清执行请求')
                require(o08['initial_directory_supported_by_before_snapshot_or_listing'] and o08['literal_package_calls'] == 0, 'C状态依据不符')
                require(o08['global_hidden_backend_nonexecution_proven'] is False, '把范围内结论扩大到后台')
                require(audit['reopen']['native_bytes_identical'] and audit['reopen']['before_native_sha256'] == audit['reopen']['after_native_sha256'] == doc['original_sha256'], '重开前后原件核对不符')
                for e in events:
                    pair = next(p for p in audit['all_request_pairs'] if p['native_call_id'] == e['native_call_id'])
                    require((pair['call_line'],pair['result_line'],pair['inner_exit_code']) == (e['call_line'],e['result_line'],e['code']), '审阅配对与原生事件不符')
                if label in ('Codex','DSH'):
                    require(len(ends) == 1 and audit['terminal_layer'] == 'native_runtime_event', '运行器终态不符')
                elif label == 'Doubao':
                    require(not ends and audit['terminal_layer'] == 'native_product_display', '展示层被冒充运行器终态')
                    end = load(ROOT / 'evidence/observability/OBS03/Doubao/page-end-display.json')
                    require(audit['terminal_evidence'] == end, '展示层终态来源不符')
                    d = end[0]['data']
                    require(d['visible'] and int(d['end_time_s']) - int(d['start_time_s']) == 83, '收尾显示内容不符')
                    require(audit['reopen']['display_after_reopen_basis'] and not audit['reopen']['new_after_reopen_screenshot'], '漏记人工观察限制')
                else:
                    messages = audit['terminal_evidence']
                    require(len(messages) == 6 and all(e['event']['status'] == 'completed' for e in messages), '消息结束反例丢失')
                    require(audit['scores']['O09'] is None and audit['scores']['O10'] is None, '消息completed被补成任务终态')
            else:
                require(all(v is None for v in audit['scores'].values()), '元宝缺证据被补分')
                require(audit['reopen']['after_native_sha256'] is None, '虚构元宝重开后原件')
        for key, amount in [('confirmed_score',sum(i['score'] or 0 for i in score['items'])), ('unknown_score',sum(i['score'] is None for i in score['items'])), ('known_failed_score',sum(i['score'] == 0 for i in score['items']))]:
            require(run[key] == score[key] == amount, '逐题累计不符')
    require(actual_changes == DELTA == {(r['label'],r['item']) for r in current['changes']} and len(current['changes']) == 4, '改分范围与变更表不符')
    for total in current['totals']:
        runs = [r for r in current['runs'] if r['label'] == total['label']]
        for key in ('confirmed_score','unknown_score','known_failed_score'): require(total[key] == sum(r[key] for r in runs), '产品累计不符')
        require(total['confirmed_score'] + total['unknown_score'] + total['known_failed_score'] == 10, '额度不守恒')
        require(total['formal_score'] is None, '产品正式分未留空')
    require(sum(t['confirmed_score'] for t in current['totals']) == current['confirmed_items'] == 38, '确认总数不符')
    require(sum(t['unknown_score'] for t in current['totals']) == current['unknown_items'] == 12, '未知总数不符')
    for entry in load(ROOT / FOLDER / 'historical-file-hashes.json')['files']:
        require(hashlib.sha256((ROOT / entry['path']).read_bytes()).hexdigest() == entry['sha256'], '历史文件被改写：' + entry['path'])
    return {'version': VERSION, 'product_runs':15, 'new_product_runs':0, 'items':50, 'confirmed':38, 'unknown':12, 'known_failed':0, 'changes':4,
            'scope':'只验证列明结构、来源与一致性；原件人工审阅、界面反馈和语义改判尚无独立第二位评委。'}


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
