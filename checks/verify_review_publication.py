"""只读核对统一记录、历史覆盖和公开审阅引用；不运行产品或诊断探针。"""
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ['唯一ID', '题目与脚本版本', '产品版本', '模型', '工具', '权限', '技能状态', '时间', '证据等级', '最终裁定']


def load(path):
    return json.loads(path.read_text())


def need(condition, message):
    if not condition:
        raise ValueError(message)


def verify_all():
    release = load(ROOT / 'results/review-publication-2026-10-30.json')
    index = load(ROOT / 'results/unified-runs-2026-10-30/总索引.json')
    coverage = load(ROOT / 'results/ledger-coverage-2026-10-30/历史台账逐条对应.json')
    ledger = load(ROOT / 'results/run-ledger-2026-10-27.json')
    ledger_ids = {r['id'] for r in ledger['runs']}
    rows = release['records']
    need(len(rows) == len({r['ledger_id'] for r in rows}) == len({r['record_id'] for r in rows}) == 114, '114条唯一对应不符')
    need({r['ledger_id'] for r in rows} == ledger_ids, '统一记录与历史台账未一一对应')
    need(release['new_product_runs'] == 0 and not release['independent_member_acceptance'], '活动或独立验收边界变化')
    need(len(index['逐题']) == index['题目数'] == 22, '22个题目目录不符')
    need(sum(c['n'] > 0 for c in index['逐题']) == index['有历史记录题目数'] == 20, '20题有历史记录不符')
    need(sum(c['n'] == 0 for c in index['逐题']) == index['仅未执行登记题目数'] == 2, '2题仅未执行不符')
    need(sum(c['n'] for c in index['逐题']) == sum(d['n'] for d in index['分维度'].values()) == index['运行记录数'] == 114, '汇总记录数不符')
    need(sum(c['u'] for c in index['逐题']) == 6, '6项准备未执行登记不符')
    for c in index['逐题']:
        out = ROOT / c['outdir']
        need(len(list((out / '运行记录').glob('*.json'))) == c['本次新生成'], '新整理记录数量不符：' + c['case'])
        need((out / (c['case'] + '-五段式任务报告.md')).is_file(), '缺共享五段式报告：' + c['case'])
        if c['复用已有']:
            need(len(list((ROOT / c['复用记录目录'] / '运行记录').glob('*.json'))) == c['复用已有'], '复用数量不符')
    need(sum(c['本次新生成'] for c in index['逐题']) == release['newly_formatted_records'] == 109, '109条新整理不符')
    need(sum(c['复用已有'] for c in index['逐题']) == release['reused_records'] == 5, '5条复用不符')
    cover_rows = {r['历史台账ID']: r for r in coverage['逐条对应']}
    need(set(cover_rows) == ledger_ids, '覆盖表与台账不一致')
    need(Counter(r['review_coverage'] for r in rows) == {'已覆盖：逐项审阅': 111, '已覆盖：用途审阅': 3}, '111逐项／3用途不符')
    need(coverage['原当前判分引用已对应记录数'] == coverage['原当前判分引用记录数'] == 93, '93条评分引用覆盖不符')
    for row in rows:
        p = ROOT / row['json']
        value = load(p)
        need(list(value) == FIELDS and value['唯一ID'] == row['record_id'], '十项字段或ID不符：' + str(p))
        md = (ROOT / row['markdown']).read_text()
        keys = [line.split('|')[1].strip() for line in md.splitlines() if line.startswith('| ')][2:]
        need(keys == FIELDS, 'Markdown十项字段顺序不符：' + row['markdown'])
        need(cover_rows[row['ledger_id']]['审阅覆盖状态'] == row['review_coverage'], '覆盖去向不符')
        for review in row['reviews']:
            need((ROOT / review).is_file(), '对应审阅缺失：' + review)
    for rel, expected in release['protected_existing_files'].items():
        p = ROOT / rel
        need(p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == expected, '旧结果／输入／证据变化：' + rel)
    diagnostic = next(load(ROOT / r['json']) for r in rows if r['record_id'] == 'WorkBuddy-OBS01-DIAG-a01')
    need('Bash 2次' in diagnostic['工具']['实际工具'] and 'Write 1次' in diagnostic['工具']['实际工具'], '诊断实际工具变化')
    need('保留1次产品原生环境诊断尝试' in diagnostic['最终裁定'], '历史诊断用途被抹去')
    old = next(load(ROOT / r['json']) for r in rows if r['record_id'] == '元宝专家模式-STOP03A-old-mismatched')
    need('保留1次历史无效尝试' in old['最终裁定'] and '不纳入有效运行比较' in old['最终裁定'], '旧无效尝试计数不符')
    repeat = load(ROOT / 'docs/故障归因/2026-10-30/证据再次复核-2026-10-30/证据复核结果.json')
    need(repeat['核查条数'] == len(repeat['核查']) == 34 and not repeat['不符项'], '本地原件再次复核记录不符')
    need(not repeat['已执行产品探针'] and not repeat['第二位成员独立验收'], '本地复核范围被扩大')
    timing = load(ROOT / 'evidence/recovery/REC01/Codex/timing.excerpt.json')['records']
    need([r['event']['payload']['type'] for r in timing] == ['task_started', 'task_complete'], '任务时间事件不符')
    start, end = [datetime.fromisoformat(r['event']['timestamp'].replace('Z', '+00:00')) for r in timing]
    need((end - start).total_seconds() == 19.425, 'REC01起止间隔不符')
    corrected = next(load(ROOT / r['json']) for r in rows if r['record_id'] == 'Codex-REC01-a01')
    need(corrected['时间'] == release['field_corrections'][0]['corrected'] and '19.425秒' in corrected['时间'], 'REC01时间补正未同步')
    return {'historical_records': 114, 'item_reviews': 111, 'purpose_reviews': 3,
            'task_directories': 22, 'tasks_with_records': 20, 'unexecuted_only_tasks': 2,
            'prepared_not_started_entries': 6, 'newly_formatted': 109, 'reused': 5,
            'protected_existing_files_unchanged': len(release['protected_existing_files']),
            'local_recheck_recorded_checks': 34, 'new_product_runs': 0,
            'scope': '公开副本结构、计数、引用与旧文件保持；不是全部内容重新判卷或未公开原件的独立认证。'}


if __name__ == '__main__':
    print(json.dumps(verify_all(), ensure_ascii=False, indent=2))
