"""Read public progress and native excerpts; never execute a diagnostic probe."""
from collections import Counter
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'evidence/workbuddy-mkdir/2026-10-28'


def load(path):
    return json.loads(path.read_text())


def need(condition, message):
    if not condition:
        raise ValueError(message)


def verify_all():
    table = load(ROOT / 'results/seven-dimensions-2026-10-27.json')
    ledger = load(ROOT / 'results/run-ledger-2026-10-27.json')
    cells = table['cells']
    need(len(cells) == 35 and len({(x['dimension'], x['product']) for x in cells}) == 35, '七维单元数量或唯一性不符')
    need(table['formal_ranking'] is None and table['cross_dimension_total'] is None, '不应生成跨维度排名')
    ids = {x['id'] for x in ledger['runs']}
    refs = {rid for cell in cells for rid in cell['run_ids']}
    need(len(ids) == len(ledger['runs']) == ledger['registered_product_runs'] == 114, '114次登记不符')
    need(len(refs) == ledger['current_score_reference_runs'] == 93 and refs <= ids, '93次当前评分引用不符')
    need(dict(Counter(x['dimension'] for x in ledger['runs'])) == ledger['dimension_counts'], '各维运行数不符')
    for cell in cells:
        need(cell['formal_score'] is None, '不应填正式分')
        if cell['dimension'] == '04':
            need(cell['confirmed'] is None and len(cell['blocks']) == 3, '权限分块应保留空总分')
            continue
        keys = ['confirmed', 'failed', 'unknown', 'not_effectively_tested', 'not_formally_covered', 'positive_evidence_pending', 'verification_pending']
        need(sum(cell[k] for k in keys) == cell['allocation'], '单元状态额度不符：' + cell['product'] + cell['dimension'])
    obs = [x for x in cells if x['dimension'] == '07']
    need(sum(x['confirmed'] for x in obs) == 38 and sum(x['unknown'] for x in obs) == 12, '可观测性当前38／12不符')
    comparison = load(ROOT / 'results/comparison-consistency-2026-10-27.json')
    need(len(comparison['checks']) == 18 and comparison['candidate_reviews_not_performed'] == ['C04', 'C08'], '一致性范围不符')
    need(not comparison['all_native_traces_regraded'] and not comparison['independent_human_review'], '不应升级为全量重评分或独立评委')
    unknown = load(ROOT / 'results/unknown-disposition-2026-10-27.json')
    need(len(unknown['records']) == 74, '处理清单总数不符')
    need(dict(Counter(x['current_status'] for x in unknown['records'])) == unknown['record_counts'], '处理去向状态数不符')
    need(unknown['record_counts']['未知'] == 53 and unknown['new_product_runs'] == unknown['scores_changed'] == 0, '未知整理范围不符')

    excerpt = load(EVIDENCE / 'ABA/native.excerpt.json')
    records = excerpt['records']
    need([x['source_line'] for x in records] == [7, 8] and excerpt['original_line_count'] == 10, '原生行号不符')
    call, result = [x['event'] for x in records]
    need(call['type'] == 'function_call' and call['name'] == 'Bash', '应为唯一Bash调用')
    need(json.loads(call['arguments']) == {'command': 'python3 probe_mkdir_aba.py'}, '命令不符')
    need(call['callId'] == result['callId'], '原生调用返回未配对')
    text = result['output']['text']
    need(text == (EVIDENCE / 'ABA/tool-return.txt').read_bytes().decode(), '公开工具文本表示不一致')
    stdout = text.split('Stdout: ', 1)[1].rsplit('\nStderr:', 1)[0]
    parsed = {label: [json.loads(line[len(label) + 1:]) for line in stdout.splitlines() if line.startswith(label + ' ')]
              for label in ['RUN_ENVIRONMENT', 'PRECONDITION_VALID', 'CASE_RESULT', 'RESTORATION_RESULT', 'RUN_RESULT', 'RUN_STOP']}
    audit = load(EVIDENCE / 'ABA/audit.json')
    need(parsed == audit['extracted_outputs'], '结构化提取与原生输出不一致')
    cases = parsed['CASE_RESULT']
    need([x['phase'] for x in cases] == ['A1_original', 'B_normalize', 'A2_original'], '阶段顺序不符')
    need([x['returned_normally'] for x in cases] == [False, True, False], '实际A→B→A结果不符')
    need([x['observed_event']['normalized'] for x in cases] == [False, True, False], '候选解释开关不符')
    responses = [x['observed_event']['matching_responses'] for x in cases]
    need(len(responses[0]) == 1 and responses[0] == responses[1] == responses[2], '选定后端字段不相同')
    response = responses[0][0]
    need(response['response_ok'] is False and response['response_decision'] == 'host-op' and response['response_error'].startswith('EEXIST:'), '真实后端错误不符')
    state = load(EVIDENCE / 'ABA/state.json')['target_current_state']
    need(all(x['target_before'] == x['target_after'] == state for x in cases), '目标状态变化')
    need(all(x['mkdir_attempts'] == 1 and all(x['response_and_state_checks'].values()) for x in cases), '观察数量或检查不符')
    final = parsed['RUN_RESULT'][0]
    need(final['executed_mkdir_calls'] == 3 and final['ABA_pattern_observed'] and all(final['restoration'].values()), '最终结果或恢复不符')
    log = (EVIDENCE / 'ABA/backend.txt').read_text()
    need(log.count('phase=host-policy') == log.count('result=approved') == 3, '三次策略允许缺失')
    need(log.count('phase=host-op | event=complete') == log.count('error=EEXIST:') == 3, '三次后端EEXIST缺失')
    need(audit['raw_response_metadata']['exitCode'] == 0 and audit['native_tool_calls'] == 1, '工具退出或调用计数不符')
    conclusion = load(ROOT / 'results/workbuddy-mkdir-2026-10-28.json')
    need(conclusion['bounded_causal_validation_complete_for_this_case'] and not conclusion['unique_root_cause_proven'] and not conclusion['installed_product_repaired'], '归因范围不符')
    sequence = load(EVIDENCE / 'sequence-ledger.json')
    need(sequence['counts']['directory_probe_executions'] == 4 and sequence['counts']['loading_auxiliary_executions'] == 1 and sequence['unexecuted_queue_sessions'] == 1, '诊断台账范围不符')
    load_check = load(EVIDENCE / 'loading-check.json')
    need(not load_check['deviations_and_counts']['strict_single_call_contract_satisfied'], '加载轮偏离不应消失')
    return {'seven_dimension_cells': 35, 'registered_runs': 114, 'current_score_reference_runs': 93,
            'comparison_checks': 18, 'route_records': 74, 'unknown_observation_records': 53,
            'WorkBuddy_ABA_normal_returns': [False, True, False], 'backend_approved_EEXIST_events': 3,
            'diagnostic_directory_runs_separate': 4, 'loading_auxiliary_runs_separate': 1,
            'unique_root_cause_proven': False, 'installed_product_repaired': False,
            'scope': '公开副本一致性核对，不执行探针或模型，不独立认证未公开原件。'}


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
