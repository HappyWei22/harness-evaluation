"""仅核对最终状态内容；不判断是否压缩，不自动产生正式评测分。"""
import argparse
import json
from pathlib import Path

CASE = Path(__file__).resolve().parents[1]


def inspect_answer(text, complete=False):
    checks = json.loads((CASE / '评测者材料/16项检查表.json').read_text())
    result = {'caseVersion': '0.1.0-draft', 'kind': 'content_check_only',
              'formalScore': None, 'compactionVerified': False,
              'note': '必须另核对完整过程、实际压缩及被替换范围；不能把通过数直接记为复杂场景正式得分。',
              'warnings': [], 'items': []}
    rows = None
    try:
        clean = text.strip()
        if clean.startswith('```') and clean.endswith('```'):
            clean = '\n'.join(clean.splitlines()[1:-1])
        value = json.loads(clean)
        rows = value.get('活动') if isinstance(value, dict) else None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            rows = None
            result['warnings'].append('不是可唯一解释的活动数组，需要人工检查完整回复。')
    except (json.JSONDecodeError, ValueError):
        result['warnings'].append('无法直接解析JSON；不得据此认定全部业务值错误。')
    if not complete:
        result['warnings'].append('尚未确认拿到完整最终回复，暂不判内容通过或失败。')
    if rows is not None:
        expected_ids = {item['活动编号'] for item in checks}
        if any(row.get('活动编号') not in expected_ids for row in rows):
            result['warnings'].append('包含额外活动或无法识别的编号，须人工复核完成条件。')
    for rule in checks:
        item = {**rule, '实际': None, '判断': '需人工复核'}
        if complete and rows is not None:
            matching = [row for row in rows if row.get('活动编号') == rule['活动编号']]
            if len(matching) > 1:
                item['说明'] = '同一编号重复，不能挑选其中正确的一条。'
            elif not matching or rule['字段'] not in matching[0]:
                item['判断'] = '不符合'
                item['说明'] = '完整可解析回复中缺少该活动或字段。'
            else:
                actual = matching[0][rule['字段']]
                item['实际'] = actual
                expected = rule['预期']
                if isinstance(expected, int):
                    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                        item['判断'] = '符合' if actual == expected else '不符合'
                    else:
                        item['说明'] = '预算不是数值，需判断是否为等价明确表达。'
                elif isinstance(actual, str):
                    item['判断'] = '符合' if actual == expected else '需人工复核'
                    if actual != expected:
                        item['说明'] = '文本不同，需排除同义表达；明确沿用旧值时人工判不符合。'
                else:
                    item['说明'] = '字段类型异常，需核对原文。'
        result['items'].append(item)
    result['contentPassCount'] = sum(x['判断'] == '符合' for x in result['items'])
    result['contentFailCount'] = sum(x['判断'] == '不符合' for x in result['items'])
    result['manualReviewCount'] = sum(x['判断'] == '需人工复核' for x in result['items'])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('answer', type=Path, help='评测者保存的最终回复文本')
    parser.add_argument('--complete-reply', action='store_true', help='评测者已核对这是完整的首次最终回复')
    args = parser.parse_args()
    print(json.dumps(inspect_answer(args.answer.read_text(), args.complete_reply), ensure_ascii=False, indent=2))
