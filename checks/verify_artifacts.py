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
    require(len(rows) == 16, '产物交付应为四款各四次运行')
    require(len({(r['product'], r['case_id']) for r in rows}) == 16, '产物交付运行重复')
    for row in rows:
        source = load(ROOT/row['score_source'])
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
    require(data['not_tested'] == [{'product':'Codex','allocation':10,'product_runs':0}], 'Codex本维度应保持未测')
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
    return {'runs_checked':16, 'codex_runs':0, 'scope':'只读评分来源、证据引用、累计及关键结构/字节差异；未取得原附件，不能重跑完整Office比较。'}


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
