"""只读检查公开的产物交付记录；不运行Office任务、模型或产品。"""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def require(ok, message):
    if not ok:
        raise ValueError(message)


def verify_all():
    data = load(ROOT/'results/artifact-delivery.json')
    rows = data['runs']
    products = {'DSH', 'WorkBuddy', '豆包办公', '元宝专家模式', 'Codex'}
    cases = {'ART01': 2, 'ART02': 2, 'ART04': 4, 'ART05': 2}
    require(len(rows) == 20, '产物交付应为五款各四次运行')
    require({(r['product'], r['case_id']) for r in rows} ==
            {(p, c) for p in products for c in cases}, '产物交付运行缺失或重复')
    require(len(data['summary']) == 5 and {x['product'] for x in data['summary']} == products,
            '五产品汇总缺失或重复')
    for row in rows:
        source = load(ROOT/row['score_source'])
        require(row['allocation'] == cases[row['case_id']] and row['product_runs'] == 1,
                '题目额度或独立运行次数不符')
        unknown = source.get('unknown_score', source.get('unknown_or_disputed_score', 0))
        failed = source.get('known_failed_score', row['allocation']-source['confirmed_score']-unknown)
        require(row['confirmed_score'] == source['confirmed_score'], '确认分来源不一致')
        require(row['known_failed_score'] == failed and row['unknown_score'] == unknown, '失败或Unknown来源不一致')
        require(row['allocation'] == row['confirmed_score']+failed+unknown, '单题额度不守恒')
        for path in row['evidence']:
            require((ROOT/path).is_file(), '缺公开产物交付证据：'+path)
    for total in data['summary']:
        chosen = [r for r in rows if r['product'] == total['product']]
        require(len(chosen) == 4, '每款应有四道题')
        for key in ['allocation', 'confirmed_score', 'known_failed_score', 'unknown_score', 'product_runs']:
            require(sum(r[key] for r in chosen) == total[key], '产物交付累计不一致：'+key)
        require(total['allocation'] == 10 and total['formal_score'] is None, '不得扩大额度或补造正式分')
    require(data['not_tested'] == [], '五款四题均已执行，不得继续将Codex标作未测')
    review = load(ROOT/data['review_source'])
    require(len(review['products']) == 5 and len(review['pending_items']) == 8,
            '限时复核产品或待定清单不符')
    expected_pending = set()
    observations = 0
    for product in review['products']:
        total = next(x for x in data['summary'] if x['product'] == product['product'])
        for key in ['confirmed_score', 'known_failed_score', 'unknown_score']:
            require(product[key] == total[key], '限时复核总分与当前汇总不一致')
        for case in product['cases']:
            source = load(ROOT/case['public_score_source'])
            active = {x['id']: x for x in case['items']}
            require(len(active) == cases[case['case']], '有效观察项缺失或重复')
            observations += len(active)
            for item in active.values():
                original = next(x for x in source['items'] if x['id'] == item['id'])
                require(item['score'] == original['score'], '有效观察项与评分来源不一致')
                if item['score'] is None:
                    expected_pending.add((product['product'], item['id']))
    require(observations == 50, '有效观察项应为50，不含移出的业务分和旧追加轮')
    require(expected_pending == {(x['product'], x['item_id']) for x in review['pending_items']},
            '待定清单与实际有效观察项不一致')
    for product in products:
        source = load(ROOT/f'evidence/artifact-delivery/ART04/{product}/score.json')
        require(next(x for x in source['items'] if x['id'] == 'ART04-3')['score'] is None,
                '共同授权范围待定不得按产品擅自补分')
    ns = {'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    fragment = load(ROOT/'evidence/artifact-delivery/ART05/豆包办公/日志行结构现场.json')
    root = ET.fromstring('<sheetData xmlns:x="'+ns['x']+'">'+fragment['xml_fragment']+'</sheetData>')
    nested = root.findall("x:row[@r='92']/x:row[@r='93']", ns)
    require(len(nested) == 1, '豆包新行嵌套的真实片段不符')
    for product in ['DSH','WorkBuddy','豆包办公','元宝专家模式']:
        report = load(ROOT/f'evidence/artifact-delivery/ART05/{product}/条目与指定位置独立核对-v02.json')
        excel = report['files'][0]
        require(excel['all_rows_are_direct_sheetData_children'] == (product != '豆包办公'), '行层级检查结论不符')
        require(report['files'][1]['chart_parts'] == 14, '图表部件路径或计数不符')
    first = load(ROOT/'evidence/artifact-delivery/ART05/元宝专家模式/首轮实际下载差异.json')
    require(first['all_three_available'] and not first['all_three_original_byte_match'], '元宝首轮取件字节差异记录不符')
    require(all(f['extra_parts'] == ['docProps/custom.xml'] and f['original_content_parts_equal'] for f in first['files']), '元宝差异不能改写为正文变化')
    codex = load(ROOT/'evidence/artifact-delivery/ART05/Codex/条目与指定位置独立核对.json')
    excel, deck, word = codex['files']
    require(excel['all_rows_are_direct_sheetData_children'] and excel['change_ids_unique'] and
            excel['direct_rows_including_header'] == 93 and excel['historical_data_rows_in_first'] == 91,
            'Codex续修行结构或历史记录数不符')
    require(excel['history_xml_exact_after_removing_new_row'] and
            deck['footer_version_matches'] == deck['footer_count'] == 112 and
            len(deck['native_chart_parts']) == 14 and deck['all_chart_payloads_unchanged'] and
            deck['notes_exact_original_plus_one_paragraph'] and
            word['document_xml_exact_three_target_replacements'], 'Codex指定修改/保留记录不符')
    footer = load(ROOT/'evidence/artifact-delivery/review/页脚版本整句范围复核.json')
    require(footer['raw_result_retained'] and footer['same_method_applied_to_all_available_saved_products'],
            '页脚观察必须保留原结果并对各可用成品同法复核')
    require(footer['products']['Codex']['approved_version_token_matches'] == 112 and
            footer['products']['Codex']['raw_full_sentence_matches'] == 0,
            'Codex实际新版与整句原检查差异未保留')
    require(all(footer['products'][p]['approved_version_token_matches'] == 0
                for p in ['DSH', 'WorkBuddy', '豆包办公']), '旧三款页脚失败观察发生变化')
    return {'runs_checked':20, 'codex_runs':4, 'effective_observations_checked':50,
            'pending_items_checked':8,
            'scope':'只读评分来源、证据引用、累计及关键结构/字节差异；未取得原附件，不能重跑完整Office比较。'}


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
