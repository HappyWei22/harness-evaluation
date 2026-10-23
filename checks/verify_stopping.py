"""只读核对公开停止材料；不运行产品程序，不补造缺失的云端现场。"""
from pathlib import Path
import hashlib, json

ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text())
def require(ok,s):
    if not ok:raise ValueError(s)
def canonical(v):return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def normalized(plan):
    d=dict(plan)
    for field,key in (('required_wal_segments',None),('expected_invariants','check_id'),('rejected_alternatives','candidate')):
        if isinstance(d.get(field),list):d[field]=sorted(d[field]) if key is None else sorted(d[field],key=lambda r:str(r.get(key)))
    return d
def verify_all():
    ledger=load(ROOT/'results/stopping-conditions.json')
    require(len(ledger['runs'])==20,'停止条件应有20个有效场景记录')
    require(len({(r['case_id'],r['product']) for r in ledger['runs']})==20,'运行记录重复')
    require(len(ledger['excluded_attempts'])==1,'旧部署不匹配尝试须单列')
    outputs=[];items=0;native_rows=0
    for r in ledger['runs']:
        score=load(ROOT/r['score_path']);case=r['case_id'];label=r['label'];out=ROOT/'evidence/stopping'/case/label
        entries=score['items'];items+=len(entries)
        require(len(entries)==score['allocation']==r['allocation'],'评分项数量或额度不符')
        confirmed=sum(x.get('score') or 0 for x in entries)
        unknown=sum(x.get('allocation',1) for x in entries if x.get('score') is None)
        failed=sum(x.get('allocation',1) for x in entries if x.get('score')==0)
        require(confirmed==score['confirmed_score']==r['confirmed_score'],'确认分值不符')
        require(unknown==score['unknown_score']==r['unknown_score'],'未知额度不符')
        require(failed==score['known_failed_score']==r['known_failed_score'],'失败额度不符')
        require(confirmed+unknown+failed==score['allocation'],'额度不守恒')
        for x in entries:
            require(x['score'] in (None,0,1),'分值类型不符')
            require(bool(x.get('attribution')) and bool(x.get('confidence_basis')),'缺判断范围/置信依据')
            for path in x['public_evidence']:require((ROOT/path).is_file(),'公开证据缺失：'+path)
        if label=='Yuanbao':
            expected={'STOP01':(1,1),'STOP02':(0,4),'STOP03A':(1,1),'STOP03B':(1,1)}[case]
            require((confirmed,unknown)==expected,'元宝缺证据状态被补分或补零')
            require(not (out/'native.excerpt.json').exists(),'不能把元宝可见命令改标为完整原生日志')
            if case=='STOP03A':
                receipt=load(out/'downloaded/yuanbao-stop03a-20261023-receipt.json');claim=receipt.pop('receipt_sha256')
                require(canonical(receipt)==claim,'元宝下载回执自哈希不符')
                require(load(out/'下载文件独立核对.json')['complete_cross_file_verification'] is False,'回执自哈希不能冒充完整交叉验收')
            if case=='STOP03B':
                log=(out/'downloaded/verify_backup_attempts.log').read_text()
                require(log.count('"status": "rejected"')==4,'备份3次拒绝及1次probe拒绝不符')
                require('probe requires a completed isolated restore' in log,'缺probe实际拒绝')
                require((out/'downloaded/derivation_blocker.log').read_text().rstrip().endswith('unready'),'推导日志阶段不符')
                draft=load(out/'downloaded/recovery_plandraft.json')
                require(draft['draft_status']=='NOT_VALIDATED_AND_NOT_VALIDATABLE_ON_CURRENT_EVIDENCE','草稿未正确标识')
                require(load(out/'下载文件独立核对.json')['complete_final_state_available'] is False,'部分日志不能标为完整终态')
        else:
            obs=load(out/'文件独立核对.json')
            require(obs['input_integrity']['pass'] is True,'本地输入完整性核对未通过')
            native=load(out/'native.excerpt.json');ns=[v['source_line'] for v in native['records']];native_rows+=len(ns)
            require(ns==sorted(set(ns)) and all(0<n<=native['original_line_count'] for n in ns),'摘录来源行号不符')
            require(bool(ns),'原生工具摘录为空')
            if case=='STOP01':
                require(obs['actual_state']['receipt'] is not None,'缺暂存验证回执事实')
            elif case=='STOP02':
                checks=obs['upstream_original_oracle']['checks']
                require(all(c['pass'] for c in checks),'批处理原文件检查存在不通过')
                counts=obs['actual_counts'];require((counts['index_rows'],counts['reject_rows'],counts['normalized_files'])==(9,9,9),'批处理实际数量不符')
            elif case=='STOP03A':
                ws=out/'snapshot';state=load(ws/'state/recovery_state.json');plan=load(ws/'work/recovery_plan.json');probe=load(ws/'work/probe_report.json');receipt=load(ws/'work/recovery_receipt.json')
                require(state['phase']=='manual_promotion_gate_closed' and state['replay_verified'] is True,'正常恢复终态不符')
                require(state['promoted'] is False and state['production_changed'] is False,'生产边界不符')
                require(probe['run_count']==2 and probe['complete'] is True and probe['identical_passes'] is True,'双探测报告不符')
                require(canonical(normalized(plan))==state['plan_sha256']==receipt['plan_sha256'],'计划摘要不对应')
                require(canonical(probe)==state['probe_sha256']==receipt['probe_sha256'],'探测摘要不对应')
                require(canonical(probe['results'])==probe['result_set_sha256'],'探测结果摘要不对应')
                claim=receipt.pop('receipt_sha256');require(canonical(receipt)==claim,'回执自哈希不符')
                require(state==obs['actual_state']['state'],'公开恢复JSON与文件核对事实不符')
                require('recovery_handoff' in obs['upstream_observations'],'不能省略原交接检查结果')
            else:
                require(obs['no_eligible_backup_in_frozen_seed']['pass'] and obs['state_unchanged']['pass'] and obs['downstream_artifacts_absent']['pass'],'受阻现场事实不符')
                seed=load(ROOT/'cases/STOP03B/input/state/recovery_state.json');state=load(out/'snapshot/state/recovery_state.json')
                require(state==seed==obs['state_unchanged']['detail'],'受阻终态不是原初始值')
        outputs.append({'product':r['product'],'case_id':case,'confirmed':confirmed,'unknown':unknown,'failed':failed,'scope':'材料一致性复核；不代替动作发生的独立认证或人工语义判分'})
    require(items==50,'预定观察项总数不为50')
    for total in ledger['totals']:
        rr=[r for r in ledger['runs'] if r['product']==total['product']]
        for field in ('confirmed_score','known_failed_score','unknown_score'):require(total[field]==sum(r[field] for r in rr),'逐产品累计不符')
        require(total['product_runs']==len(rr)==4,'产品有效场景数不符')
    backups=load(ROOT/'cases/STOP03B/input/evidence/backups.json')['backups']
    require(not any(r['timeline']==2 and all(r[k]=='verified' for k in ('manifest_status','file_status','wal_manifest_status')) for r in backups),'受阻冻结样本出现合格备份')
    return {'valid_scenario_runs':20,'observation_items':items,'native_excerpt_rows':native_rows,'excluded_deployment_attempts':1,'cases':outputs,'limits':'只核对公开材料、摘要和留空状态；不运行模型、控制器或原题转换；不证明未公开原件、官方整套环境或内部根因。'}

if __name__=='__main__':print(json.dumps(verify_all(),ensure_ascii=False,indent=2))
