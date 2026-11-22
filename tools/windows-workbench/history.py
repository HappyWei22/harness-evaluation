"""Read-only presentation of the existing four DSH runs."""

from store import PROJECT, read
from runner import basis

ROOT = PROJECT / "证据" / "DSH-ART04-页脚-v2"


def replays():
    timing_path = ROOT / "串行时序.json"
    data = read(timing_path) or {}
    records = {}
    for row in data.get("时序", []):
        id_ = row["运行ID"]
        folder = ROOT / "运行" / id_
        record_path = folder / "运行记录.json"
        score_path = folder / "逐页页脚.json"
        record = read(record_path)
        if not record:
            continue
        verdict = record.get("最终裁定", {})
        files = verdict.get("交付") or []
        got = sum(bool(file.get("成功传回", file.get("exists"))) for file in files)
        summary = (read(score_path) or {}).get("汇总", {})
        good = summary.get("两者同时正确")
        pages = summary.get("观察页数")
        score = f"页脚正确 {good}/{pages} 页" if good is not None and pages else "页脚结果未知"
        events = []
        for stage, field, action, result in (
            (0, "开场", "宿主机已开场", "开始准备来宾环境。"),
            (1, "发送控件调用", "已调用发送控件", "等待产品原生回执。"),
            (1, "原生接收", "产品已收到题面", "已核对同一会话中的原生用户消息。"),
            (2, "观察到同轮终态", "产品已结束本轮", "宿主机观察到该轮原生终态。"),
            (3, "归档", "已保存交付物与证据", f"已回收 {got}/{len(files)} 件声明的交付文件。"),
            (4, "关机", "虚拟机已关机", "宿主机已记录关机。"),
            (4, "恢复", "基线已恢复", "已有检查点恢复记录。"),
            (4, "重新启动", "虚拟机已重新启动", "已有来宾启动时间。"),
            (5, "关账", "账本已关账", "原记录已核对归档和清理回执。"),
        ):
            if row.get(field):
                events.append(dict(job=id_, seq=len(events)+1, source_key=field, stage=stage,
                                   at=row[field], action=action, result=result, kind="info",
                                   source=basis(timing_path, {field: row[field], "运行ID": id_})))
        records[id_] = {
            "id": id_, "source": "历史回放", "activity": "产品运行", "product": "DSH", "baseline": "v3",
            "title": "ART04 三文件修订与多轮交付", "version": record["题目与脚本版本"]["题目"],
            "condition": row["条件"].partition("-")[2], "state": "closed", "stage": 5,
            "started_at": row["开场"], "ended_at": row["关账"], "score": score,
            "collected": got, "expected": len(files), "files": files, "run_id": id_,
            "latest": events[-3:][::-1], "last_event_at": events[-1]["at"], "events": events,
            "record": record, "sources": [basis(record_path), basis(score_path), basis(timing_path),
                                          basis(folder / "原生过程索引.json"), basis(ROOT / "批次报告.md")],
        }
    return records
