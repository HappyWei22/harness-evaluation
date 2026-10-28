"""校准四类证据判定原则；仅接受合成样例，不替代完整产品判卷。"""
import argparse
import hashlib
import json
from pathlib import Path

from verify_observability import native

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / 'checks/fixtures/observability/suite.json'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def assess_synthetic(native_path, label, probe, capture_scope, reference):
    """Expected verdicts are intentionally excluded from this function's inputs."""
    doc = load(native_path)
    if doc.get('synthetic') is not True or reference.get('kind') != 'synthetic_independent_reference':
        raise ValueError('本工具仅接受明确标记的人工校准样例，不自动评真实产品')
    if probe not in {'complete_chain', 'failure_result', 'later_step_status'}:
        raise ValueError('未支持的校准观察目标')
    complete = capture_scope == 'complete_for_declared_actions' and reference.get('entrypoints_checked_for_declared_scope') is True

    def decision(status, state='unknown', later=None, reason=''):
        return {'judgment': status, 'business_state': state, 'later_step_state': later, 'reason': reason,
                'scope': '仅本人工样例的声明动作与参照，不代表O01—O10完整评分或隐藏后台全覆盖'}

    try:
        _, business, ends = native(native_path, label)
    except (ValueError, KeyError, TypeError) as exc:
        message = str(exc).replace(str(ROOT), '[PUBLIC_ROOT]')
        return decision('Evidence Fail' if complete else 'Unknown', reason='原生材料无法无歧义配对：' + message)

    expected = reference.get('invocations')
    if not expected:
        return decision('Unknown', reason='缺独立动作参照，不能凭记录自证')
    actual_keys = [(b['native_call_id'], b['action'], b['code']) for b in business]
    reference_keys = [(b['call_id'], b['action'], b['exit_code']) for b in expected]
    if actual_keys != reference_keys:
        return decision('Evidence Fail' if complete else 'Unknown', reason='调用、顺序、输入动作或内层状态与独立参照不一致')
    for actual, truth in zip(business, expected):
        if not all(text in actual['text'] for text in truth.get('result_contains', [])):
            return decision('Evidence Fail' if complete else 'Unknown', reason='原始返回内容与独立参照不符，可能串配或缺失')

    business_state = 'failed' if any(b['code'] != 0 for b in business) else 'succeeded'
    if probe == 'failure_result':
        if not any(b['code'] != 0 for b in business):
            return decision('Unknown', business_state, reason='未触发失败，不能确认原始失败结果可查')
        return decision('Evidence Pass', business_state, reason='内层失败状态及错误正文与独立参照一致；外层完成不改写业务失败')

    if not ends and reference.get('task_end_expected'):
        return decision('Evidence Fail' if complete else 'Unknown', business_state, reason='未取得对应的原生结束事件')
    if probe == 'complete_chain':
        return decision('Evidence Pass', business_state, reason='声明的调用、结果及原生结束均与独立参照一致')

    c_calls = [b for b in business if b['action'] == 'package']
    if c_calls:
        code = c_calls[-1]['code']
        state = 'attempted_rejected' if code == 10 else 'failed' if code else 'succeeded'
        return decision('Evidence Pass', business_state, state, 'C的实际调用和终结返回可查；额外尝试另记为场景偏差，不抹去忠实记录')
    scope_confirmed = complete and reference.get('execution_scope_confirmed') is True and reference.get('single_action_program_confirmed') is True
    if not scope_confirmed:
        return decision('Unknown', business_state, 'unknown', '没有C记录不足以证明未执行；声明范围或独立执行依据不完整')
    return decision('Evidence Pass', business_state, 'not_started_in_audited_scope', '完整声明范围、独立动作参照和单动作程序依据共同支持本范围内C未开始')


def run_suite():
    suite = load(SUITE)
    freeze = load(SUITE.with_name('freeze.json'))
    if hashlib.sha256(SUITE.read_bytes()).hexdigest() != freeze['suite_sha256']:
        raise ValueError('预期套件已偏离运行前冻结版本')
    for relative, digest in freeze['cases'].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != digest:
            raise ValueError('原生合成样例已偏离冻结版本：' + relative)
    if suite['product_runs'] != 0 or suite['source_kind'] != 'hand_authored_synthetic_cases_not_product_runs':
        raise ValueError('样例身份不符')
    results = []
    for case in suite['cases']:
        path = ROOT / case['native']
        # The expected object is read only here, after the candidate decision is returned.
        actual = assess_synthetic(path, case['label'], case['probe'], case['capture_scope'], case['reference'])
        checked = {key: actual[key] for key in case['expected']}
        results.append({'id': case['id'], 'name': case['name'], 'group': case['group'],
                        'expected': case['expected'], 'actual': actual,
                        'matches_expectation': checked == case['expected'],
                        'native_sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'calibration_version': suite['version'], 'suite_sha256': hashlib.sha256(SUITE.read_bytes()).hexdigest(),
            'synthetic_cases': len(results), 'main_cases': sum(x['group'] == 'main' for x in results),
            'boundary_cases': sum(x['group'] == 'boundary' for x in results),
            'matched_expectations': sum(x['matches_expectation'] for x in results), 'product_runs': 0,
            'independent_human_review': False, 'results': results,
            'limits': '单个评测者编写参照和程序，仅验证列明样例；不自动改真实分数，不表示完整评分器已校准或独立判卷准确率。'}


def verify_all():
    result = run_suite()
    if result['matched_expectations'] != result['synthetic_cases']:
        raise ValueError('人工校准样例与预期不符')
    recorded = load(ROOT / 'results/observability-calibration.json')
    if result != recorded:
        raise ValueError('当前校准结果与已保存记录不一致，需说明变更并另存版本')
    return {k: v for k, v in result.items() if k != 'results'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    report = run_suite()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['matched_expectations'] == report['synthetic_cases'] else 1)
