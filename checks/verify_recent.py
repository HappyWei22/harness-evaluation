"""只读核对新增REC01/REC02与CTX02公开材料，不运行产品或输入脚本。"""
import importlib.util
import json
from pathlib import Path
import re
import verify as common

ROOT = Path(__file__).resolve().parents[1]

def load(p): return json.loads(p.read_text(encoding='utf-8'))
def same(a,b): return json.dumps(a,ensure_ascii=False,sort_keys=True,separators=(',',':')) == json.dumps(b,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def require(ok,reason): common.require(ok,reason)
def oracle(case,name):
    spec=importlib.util.spec_from_file_location('oracle_'+case,ROOT/'cases'/case/'checks'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def native(p):
    records=common.excerpt(p)
    pairs=common.pairs(records)
    return records,pairs

def codex_values(pair):
    found=[]
    for block in pair['result']['payload']['output']:
        try: value=json.loads(block.get('text',''))
        except ValueError: continue
        if isinstance(value,dict) and 'check' in value:
            require(value['result']['status']=='fulfilled','并行只读检查没有正常返回')
            value=value['result']['value']
        if isinstance(value,dict) and 'exit_code' in value: found.append(value)
    require(bool(found),'缺Codex实际命令结果')
    return found

def recovery():
    ledger=load(ROOT/'results/failure-recovery.json')
    base_oracle=oracle('REC01','核对交付物.py')
    batch_oracle=oracle('REC02','核对阶段文件.py')
    require(len(ledger['runs'])==10,'应有两题各五款运行')
    outputs=[];judgments=0
    for run in ledger['runs']:
        product,label,case=run['product'],run['label'],run['case_id']
        directory=ROOT/Path(run['score_path']).parent
        score=load(ROOT/run['score_path'])
        items=score['items'];judgments+=len(items)
        require(len(items)==run['case_max'],'评分项数量不符')
        confirmed=sum(i['score'] or 0 for i in items)
        unknown=sum(i['score'] is None for i in items)
        require(confirmed==score['confirmed_score']==run['confirmed_score'],'确认分值不符')
        require(unknown==score['unknown_points']==run['unknown_points'],'未知额度不符')
        require(confirmed+unknown==run['case_max'],'本轮额度不守恒')
        for item in items:
            require(item.get('confidence_basis') or item.get('confidence'),'缺置信依据')
            require(item.get('attribution') or item.get('attribution_level'),'缺归因边界')
            require(item['verification_method'],'缺复核办法')
            for e in item['public_evidence']: require((ROOT/e).is_file(),'缺公开证据：'+e)
            require((item['status']=='unknown')==(item['score'] is None),'未知分值未留空')
        if case=='REC01':
            snap=directory/'snapshot'
            artifact=snap/'outputs/registration_summary.json'
            script=snap/'recovery_export.py'
            checked=base_oracle.check(artifact,script if script.exists() else None)
            require(checked['content_matches'],'基础交付物不符合固定标准')
            if label=='Yuanbao':
                require(confirmed==0 and unknown==3,'元宝基础题未知状态改变')
                require(not script.exists(),'不能把准备脚本当云端实际脚本')
            elif label=='Doubao':
                text=(directory/'界面核对摘录.md').read_text()
                require('legacy-json' in text and 'E_UNSUPPORTED_FORMAT' in text and 'TOTAL_PARTICIPANTS=39' in text,'豆包界面摘录缺关键内容')
                require(checked['script_unchanged'],'豆包实际脚本与版本不符')
            else:
                rows,pairs=native(directory/'native.excerpt.json')
                legacy=[p for p in pairs if 'recovery_export.py --format legacy-json' in common.command(p)]
                retry=[p for p in pairs if 'recovery_export.py --format json' in common.command(p)]
                require(len(legacy)==len(retry)==1,'基础题失败及更正调用次数不符')
                require(legacy[0]['line']<retry[0]['line'],'基础调用顺序不符')
                fail,success=common.text_of(legacy[0]['result']),common.text_of(retry[0]['result'])
                require('ERROR_CODE=E_UNSUPPORTED_FORMAT' in fail and 'STATUS=OK' in success and 'TOTAL_PARTICIPANTS=39' in success,'关键回传不符')
                if label=='Codex':
                    require(codex_values(legacy[0])[0]['exit_code']==7 and codex_values(retry[0])[0]['exit_code']==0,'Codex基础退出状态不符')
                elif label=='WorkBuddy':require('Exit Code: 7' in fail and 'Exit Code: 0' in success,'WB基础退出状态不符')
                else:
                    settled=[x['data']['detail'] for x in rows.values() if x.get('type')=='job/settled']
                    require('exit code: 7' in settled and 'exit code: 0' in settled,'DSH基础执行状态不符')
                require(checked['script_unchanged'],'原版脚本不符')
        else:
            first=directory/'failure/workspace';final=directory/'complete/workspace'
            for phase,ws in [('failure',first),('complete',final)]:
                check=batch_oracle.inspect(ws,phase,first if phase=='complete' else None)
                require(check['all_available_checks_passed'] and not check['unknown_checks'],'前后阶段文件不符：'+label)
            initial=load(first/'.recovery/state.json');state=load(final/'.recovery/state.json')
            events=[json.loads(l) for l in (final/'.recovery/events.jsonl').read_text().splitlines()]
            require([(e['action'],e.get('batch')) for e in events]==[('task_started',None),('batch_completed','A'),('batch_interrupted','B'),('partial_discarded','B'),('batch_completed','B'),('batch_completed','C'),('manifest_created',None)],'批次事件顺序不符')
            if label in ['Codex','DSH','WorkBuddy']:
                rows,pairs=native(directory/'complete/native.excerpt.json')
                expected=['start','status','discard-partial --batch B','run-batch --batch B','run-batch --batch C','finalize','verify']
                if label=='Codex':
                    commands=[x['payload']['item'] for x in rows.values() if x.get('type')=='event_msg']
                    task_commands=[x['command'][2].split('python3 recovery_batch.py ',1)[1] for x in commands if x['command'][2].startswith('python3 recovery_batch.py ')]
                    require(task_commands[:6]==expected[:6] and sorted(task_commands[6:])==['status','verify'],'Codex恢复命令顺序不符')
                    values=[v for p in pairs for v in codex_values(p)]
                    require(len(values)==9 and sorted((v['exit_code'],v['output']) for v in values)==sorted((x['exit_code'],x['aggregated_output']) for x in commands),'Codex返回与执行事件不一致')
                    verify=next(v for v in values if 'STATUS=PASS\nCHECKS_PASSED=10' in v['output'])
                    require(verify['exit_code']==0,'Codex验证退出状态不符')
                else:
                    script_pairs=[p for p in pairs if 'python3 recovery_batch.py ' in common.command(p)]
                    actual=[common.command(p).split('python3 recovery_batch.py ',1)[1].strip() for p in script_pairs]
                    require(actual[:7]==expected,'恢复操作顺序不符：'+label)
                    require(actual.count('start')==1 and not any(x.startswith('run-batch --batch A') for x in actual),'重新启动或重生成A')
                    for p in script_pairs[1:]:
                        result=common.text_of(p['result'])
                        require('STATUS=OK' in result or 'STATUS=PASS' in result or '"batches"' in result,'缺成功或状态返回')
                    output='\n'.join(common.text_of(p['result']) for p in script_pairs if common.command(p).endswith(' verify'))
                    require('STATUS=PASS' in output and 'CHECKS_PASSED=10' in output,'未见验证通过返回')
                require(confirmed==12 and unknown==0,'完整证据的评分状态改变')
            else:
                ui=load(directory/'complete/客户端过程核对.json')
                if label=='Doubao':
                    ops=ui['operations'];require(len(ops)==6,'界面恢复操作不齐')
                    require(ops[1]['visible_result_fields']['REMOVED_PATH']=='outputs/batch_B.partial.json','界面清理路径不符')
                    v=ops[-1]['visible_result_fields'];require(v['STATUS']=='PASS' and v['CHECKS_PASSED']==10 and all(v['CHECKS'].values()),'界面验证反馈不符')
                    require(confirmed==12 and unknown==0,'豆包评分状态改变')
                else:
                    require(not ui['tool_result_body_available'],'元宝原生结果证据状态改变')
                    require({i['id'] for i in items if i['score'] is None}=={'P4','P5','P8','P11','P12'},'元宝未知条目改变')
                    require(confirmed==7 and unknown==5,'元宝复杂题额度改变')
        outputs.append({'case_id':case,'product':product,'confirmed_points':confirmed,'unknown_points':unknown,'scope':'本次材料支持范围，不作纯Harness归因或排名'})
    require(judgments==75,'恢复评分条目总数不符')
    return {'judgments_checked':judgments,'runs':outputs}

def context_compression():
    ledger=load(ROOT/'results/context-compression.json')
    expected=load(ROOT/'cases/CTX02/评测者材料/预期状态.json')
    prompts=sorted((ROOT/'cases/CTX02/提示词').glob('*.md'))[:5]
    normalize=lambda text: re.sub(r'\s+','',text)
    require(len(ledger['runs'])==5 and ledger['formal_score'] is None,'探索性评分状态错误')
    for run in ledger['runs']:
        obs=load(ROOT/run['observation_path']);final=load(ROOT/run['final_path'])
        require(same(final,expected),'CTX02最终内容与标准不符')
        require(obs['formal_score'] is None,'不能给探索题补正式16分')
        directory=(ROOT/run['observation_path']).parent
        if run['label']=='DSH':
            summary=load(directory/'compaction-summary.json');block=load(directory/'post-request-summary-block.json')
            require(block['text'] in summary['summary_blocks'],'DSH后续正文选定片段与摘要不符')
            require(obs['compaction_confirmed'] is True,'DSH压缩状态不符')
        elif run['label']=='WorkBuddy':
            packed=load(directory/'compaction-content-excerpt.json')
            require(len(packed['five_user_queries'])==5,'WB打包历史不齐')
            require(all(normalize(p.read_text()) in normalize(t) for p,t in zip(prompts,packed['five_user_queries'])),'WB压缩记录没有保留完整五轮输入')
            require(obs['compaction_confirmed'] is True,'WB压缩状态不符')
        elif run['label']=='Codex':
            history=load(directory/'replacement-history-excerpt.json');check=load(directory/'compaction-check.json')
            require(len(history['business_messages'])==5 and check['nativeCompactionCompleted'],'Codex保留历史/压缩事件不齐')
            require(all(normalize(p.read_text()) in normalize(t['text']) for p,t in zip(prompts,history['business_messages'])),'Codex替换历史没有保留完整五轮输入')
        else:require(obs['compaction_confirmed'] is None,'不能把内容正确写成已确认压缩')
    return {'products_checked':5,'candidate_content_items':80,'formal_score':None,'scope':'核对最终内容及公开压缩片段，不证明五款共同压缩条件或服务端完整请求。'}

def verify_all(): return {'failure_recovery':recovery(),'context_compression':context_compression()}

if __name__=='__main__':
    try: print(json.dumps(verify_all(),ensure_ascii=False,indent=2))
    except (ValueError,KeyError,IndexError,OSError,AttributeError,AssertionError) as exc:
        raise SystemExit('新增材料核对未通过：'+str(exc))
