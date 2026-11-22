"""One serial worker using simulated receipts or the existing Windows DSH gate."""

from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from store import GATE, Queue, TaskLayoutError, inside, read, sha256_bytes, sha256_file
from run_dsh_task import ledger_path, request_store
from startup_lock import StartupLockBusy, acquire_startup_lock


def basis(path=None, data=None, label=None):
    return {"label": label or (Path(path).name if path else "后台记录"),
            "path": str(path) if path else None, "data": data}


def block(queue, job, stage, reason, source=None, detail=None, kind="error"):
    key = "blocked:" + sha256_bytes(reason.encode("utf-8"))[:16]
    queue.event(job["id"], key, stage, "队列已暂停", reason, source=source, kind=kind)
    queue.update(job["id"], "blocked", detail, reason)


SIM_STEPS = (
    ("prepared", 0, "准备环境完成", "已校验冻结题包及输入文件。"),
    ("invoke", 1, "已调用发送控件", "等待产品确认。"),
    ("accepted", 1, "产品已收到题面", "模拟原生回执已记录，正式提交数为零。"),
    ("processing", 2, "等待产品结束本轮", "已开始观察同一轮的终态。"),
    ("terminated", 2, "产品已结束本轮", "已取得模拟的同轮结束回执。"),
    ("collected", 3, "回收并保存交付文件", "宿主机已保存模拟交付文件和取件清单。"),
    ("restore_requested", 4, "请求恢复虚拟机", "模拟恢复控制器已记录请求。"),
    ("restored", 4, "基线已恢复", "模拟检查点恢复成功。"),
    ("booted", 4, "虚拟机已重新启动", "模拟启动回执已返回。"),
    ("verified", 4, "恢复核验通过", "模拟核验记录显示桌面就绪，工作区、会话和任务均无残留。"),
    ("closed", 5, "已关账", "模拟账本已保存提交、交付和清理结果。"),
)


class Simulation:
    def __init__(self, queue):
        self.queue = queue
        self.seen = set()

    def tick(self, job, fresh):
        id_ = job["id"]
        events = self.queue.events(id_)
        seen = {event["source_key"] for event in events}
        restarting = id_ not in self.seen and not fresh
        self.seen.add(id_)
        task = job["task"]
        scenario = task["scenario"] if isinstance(task["scenario"], dict) else {}
        if restarting and "invoke" in seen and "accepted" not in seen:
            block(self.queue, job, 1, "重启时发现已调用发送控件，接收回执缺失。保留模拟现场并暂停。",
                  next(event["source"] for event in events if event["source_key"] == "invoke"))
            return
        if restarting and "accepted" in seen and "closed" not in seen:
            self.queue.event(id_, "reconciled", events[-1]["stage"], "重启后继续原场次",
                             "按原请求号确认已有模拟接收回执，继续等待或收尾。",
                             source=basis(data={"请求号": id_, "依据": "accepted", "模拟发送次数": 1}))
        if events:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(events[-1]["at"])).total_seconds()
            delay = float(scenario.get("step_seconds", 1.1))
            if events[-1]["source_key"] == "invoke":
                delay = float(scenario.get("accept_seconds", delay))
            if elapsed < delay and "closed" not in seen:
                return
        else:
            self.queue.verify(job)
        detail = {"请求号": id_, "来源": "模拟自测"}
        for key, stage, action, result in SIM_STEPS:
            if key in seen:
                continue
            if key == "accepted" and scenario.get("fault") == "timeout":
                failure = {"模拟": True, "超时阶段": "产品接收", "报告时间": datetime.now(timezone.utc).isoformat()}
                self.queue.event(id_, "timeout", 1, "后台报告超时", "在模拟时限内未取得接收回执。",
                                 source=basis(data=failure), kind="timeout")
                block(self.queue, job, 1, "模拟接收超时，提交结果存疑。", basis(data=failure), kind="timeout")
                return
            if key == "restored" and scenario.get("fault") == "restore":
                failure = {"模拟": True, "检查点": job["baseline"], "Verdict": "fail",
                           "Error": "模拟 Hyper-V 恢复拒绝，未取得新启动记录"}
                block(self.queue, job, 4, failure["Error"], basis(data=failure, label="模拟恢复报告"))
                return
            if key in {"invoke", "accepted"}:
                detail["模拟发送次数"] = 1
            if key == "collected":
                results = self.queue.root / "simulation-results" / id_
                files = []
                for output in task["outputs"]:
                    dest = inside(results, output)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(task["expect"].get(output, "模拟交付文件"), encoding="utf-8")
                    files.append({"path": output, "status": "present", "exists": True,
                                  "sha256": sha256_file(dest), "bytes": dest.stat().st_size})
                self.queue.update(id_, "running", {"files": files, "collected": len(files),
                                                   "score": "模拟交付已生成"})
                detail["清单"] = files
            self.queue.event(id_, key, stage, action, result, source=basis(data=detail, label="模拟回执"))
            if key != "closed":
                return
            break
        result = {**job["result"], "run_id": id_, "simulated_submissions": 1,
                  "score": job["result"].get("score", "模拟交付已生成")}
        self.queue.update(id_, "closed", result)


class DSH:
    def __init__(self, queue, control, wait_signin):
        self.queue = queue
        self.control = control
        self.wait_signin = wait_signin
        self.process = None
        self.process_kind = None

    def launch(self, job, resume=False):
        id_ = job["id"]
        command = [sys.executable, "-B", "-X", "utf8", str(GATE / "run_dsh_task.py"),
                   "--control-root", str(self.control), "resume" if resume else "run",
                   "--request-id", id_]
        if resume:
            command += ["--archive-only"]
        else:
            self.queue.verify(job)
            command += ["--task", job["package"], "--baseline", job["baseline"],
                        "--wait-signin", str(self.wait_signin)]
        directory = self.queue.root / "controller" / id_
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / ("resume.log" if resume else "run.log")
        with log.open("ab", buffering=0) as fd:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=fd,
                                            stderr=subprocess.STDOUT,
                                            env={**os.environ, "HARNESS_WORKBENCH_PROGRESS": "1"})
        self.process_kind = "resume" if resume else "run"
        return log

    def ledger(self, attempt):
        path = ledger_path(self.control)
        if not attempt or not path.is_file():
            return {}
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt,)).fetchone()
            if row is None:
                return {}
            data = dict(row)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "attempt_completion" in tables:
                row = db.execute("SELECT evidence_json FROM attempt_completion WHERE attempt_id=?", (attempt,)).fetchone()
                if row:
                    data["completion_receipts"] = json.loads(row[0])
            return data

    def host_busy(self):
        try:
            with acquire_startup_lock(self.control / "startup-lock.sqlite3"):
                return False
        except StartupLockBusy:
            return True

    def observe(self, job, stage):
        id_ = job["id"]
        report_path = stage / "report.json"
        report = read(report_path) or {}
        sources = []
        for path in sorted((stage / "progress").glob("*/*.json")):
            item = read(path)
            if item and item.get("Report"):
                sources.append((path, item["Report"], item.get("ObservedAt")))
        if report:
            sources.append((report_path, report, report.get("FinishedAt")))
        seen = {event["source_key"] for event in self.queue.events(id_)}

        def event(key, step, action, result, path, at=None, detail=None, kind="info"):
            if key in seen:
                return
            self.queue.event(id_, key, step, action, result, at=at,
                             source=basis(path, detail), kind=kind)
            seen.add(key)

        phase_names = {
            "validate_baseline": "检查基线配置", "shutdown_before_restore": "请求关机以准备环境",
            "restore_baseline": "开始恢复起始基线", "desktop_session": "检查来宾桌面",
            "await_operator_signin": "等待操作者登录虚拟机", "isolation_preflight": "执行接口隔离预检",
            "prepare_workspace": "准备来宾输入和工作区", "launch_task": "启动来宾题目流程",
            "await_prompt": "等待题面粘贴并读回", "await_authorisation": "等待单次发送授权",
            "collect_outputs": "开始回收交付文件", "cleanup": "开始清理来宾现场",
            "final_restore": "开始末尾恢复",
        }
        phase_steps = {"await_authorisation": 1, "collect_outputs": 3, "cleanup": 4, "final_restore": 4}
        for path, data, observed in sources:
            if data.get("RequestId") != id_:
                continue
            phase = data.get("Stage")
            if data.get("StartedAt"):
                event("host_started", 0, "宿主机控制器已开始", "已读取此场的 DSH 运行配置。", path,
                      data["StartedAt"])
            if data.get("BaselineRestored"):
                first_restore = stage / "restore" / "first.json"
                event("initial_restore", 0, "起始基线已恢复", "已取得起始恢复回执。", first_restore,
                      (read(first_restore) or {}).get("FinishedAt") or observed)
            if phase in phase_names and phase_steps.get(phase, 0) == 0:
                event("phase-" + phase, 0, phase_names[phase], "宿主机已进入此阶段。", path, observed)
            if (data.get("Workspace") or {}).get("PromptMatches"):
                event("ready", 0, "工作区与题面读回已核对", "已取得来宾工作区和题面指纹回执。",
                      stage / "ready.json", observed)
            if data.get("SubmissionPermitted"):
                auth = read(stage / "go.json") or {}
                event("permit", 1, "宿主机已放行发送", "等待发送控件的调用回执。", stage / "go.json",
                      auth.get("authorized_at"))
            dispatch = data.get("Dispatch") or {}
            if dispatch.get("InvokeStartedAt"):
                event("invoke", 1, "已调用发送控件", "等待 DSH 原生接收确认。", path,
                      dispatch["InvokeStartedAt"], dispatch)
            completion = data.get("Completion") or {}
            acceptance = data.get("NativeAcceptance") or completion.get("Acceptance") or {}
            if acceptance.get("AcceptanceVerified"):
                event("accepted", 1, "产品已收到题面", "原生会话确认本场题面进入 DSH。", path,
                      observed, acceptance)
                if not completion.get("NativeTurnEnd"):
                    event("processing", 2, "等待产品结束本轮", "正在观察同一会话中该轮的结束回执。", path, observed)
            if data.get("ObservationLimitAt"):
                event("timeout", 2 if acceptance.get("AcceptanceVerified") else 1, "后台报告达到时限",
                      "宿主机记录本轮观察时限已到。", path, data["ObservationLimitAt"], kind="timeout")
            if data.get("CancelRequestedAt"):
                event("cancel", 2, "已请求结束本轮", "等待产品原生结束回执。", path, data["CancelRequestedAt"])
            if completion.get("NativeTurnEnd"):
                statuses = {"completed": "已完成", "failed": "失败", "cancelled": "已取消", "canceled": "已取消",
                            "interrupted": "已中断", "error": "出错"}
                status = completion.get("TurnStatus")
                event("terminated", 2, "产品已结束本轮", f"原生终态 · {statuses.get(status, status or '未知')}。",
                      path, data.get("TurnObservedAt"), completion["NativeTurnEnd"])
            if phase in phase_names and phase_steps.get(phase, 0) > 0:
                event("phase-" + phase, phase_steps[phase], phase_names[phase], "宿主机已进入此阶段。", path, observed)
            for output in data.get("Outputs") or []:
                name = output["Path"]
                status = ("已回收，宿主机与来宾指纹一致" if output.get("HostMatches") else
                          "来宾确认文件缺失" if output.get("Missing") else "回收未核验")
                event("file:" + name, 3, status, name, path, observed, output)
            if data.get("ArchivedAt"):
                event("archived", 3, "已保存交付物与过程证据", "取件清单、回复及来宾现场已归档。", path, data["ArchivedAt"])
            if data.get("PoweredOffAt"):
                event("poweroff", 4, "来宾已关机", "已取得虚拟机关机回执。", path, data["PoweredOffAt"])
            if data.get("ScenePreserved"):
                step = max((row["stage"] for row in self.queue.events(id_)), default=0)
                event("preserved", step, "宿主机保留当前现场",
                      data.get("Error") or "此场停在证据未齐的阶段。", path, data.get("FinishedAt"), kind="error")

        restore_path = stage / "restore" / "final.json"
        final = read(restore_path) or {}
        for key, field, action, result in (
            ("restore_request", "StartedAt", "发起恢复请求", "已进入恢复控制器。"),
            ("restore_applied", "RestoreAppliedAt", "基线已恢复", "Hyper-V 返回了检查点恢复结果。"),
            ("boot", "StartReturnedAt", "虚拟机启动请求已返回", "正在核对来宾启动与残留。"),
        ):
            if final.get(field):
                event(key, 4, action, result, restore_path, final[field])
        if final.get("Verdict") == "pass":
            guest = final.get("Guest") or {}
            result = ("已确认交互桌面就绪，且本场无残留。" if guest.get("VerifiedInteractiveSession")
                      else "已检查来宾启动和本场残留。")
            event("restore_verified", 4, "恢复与残留核验通过", result,
                  restore_path, final.get("FinishedAt"), guest)
        return report

    def tick(self, job, fresh):
        id_ = job["id"]
        store = request_store(self.control, id_)
        stage = store / "stage"
        log_dir = self.queue.root / "controller" / id_
        if fresh:
            log = self.launch(job)
            self.queue.event(id_, "launch", 0, "启动单题控制器", "等待宿主机接管；Windows 可能要求确认提权。",
                             source=basis(log))
            return
        report = self.observe(job, stage)
        state_path = store / "state.json"
        state = read(state_path) or {}
        attempt = state.get("attempt_id")
        ledger = self.ledger(attempt)
        detail = self.result(job, state, report, ledger, stage)
        if (state.get("stage") == "closed" and ledger.get("kind") == "formal" and ledger.get("closed_at")
                and set(ledger.get("completion_receipts", {})) >= {"accepted", "terminated", "archived", "cleaned"}):
            if self.process is not None and self.process.poll() is None:
                return
            self.queue.event(id_, "closed", 5, "账本已关账", "已保存正式提交、产品终态、回收和清理回执。",
                             at=ledger["closed_at"], source=basis(data=ledger, label="宿主运行账本查询结果"))
            self.queue.update(id_, "closed", detail)
            self.process = self.process_kind = None
            return
        self.queue.update(id_, job["state"], detail, job["reason"])
        if self.process is not None and self.process.poll() is None:
            return
        if self.host_busy():
            if self.process is not None and job["state"] != "blocked":
                block(self.queue, job, 0, "宿主机启动锁被占用，此场暂停，请检查已有运行。",
                      basis(log_dir / "run.log"), detail)
            return
        cleanup = report.get("FinalRestore") or {}
        can_close = (state.get("stage") in {"dispatched", "completed", "retrieved"}
                     and report.get("FinishedAt") and report.get("ArchivedAt")
                     and report.get("Verdict") == "pass" and cleanup.get("Verdict") == "pass"
                     and report.get("FormalSubmissions") == 1 and not report.get("CleanupError"))
        if can_close and self.process is None:
            if job["state"] == "running" or job["reason"] == RESTART_UNCONFIRMED:
                self.queue.event(id_, "resume_started", 5, "核对已有收尾凭据",
                                 "按原请求号尝试关账，仅使用已归档证据。", source=basis(stage / "report.json"))
                self.queue.update(id_, "running", detail)
                self.launch(job, resume=True)
                return
        if job["state"] == "blocked":
            return
        events = self.queue.events(id_)
        step = max((event["stage"] for event in events), default=0)
        if self.process is None and not report.get("FinishedAt") and any(e["source_key"] == "accepted" for e in events):
            self.queue.event(id_, "detached_stage", step, "后台已重启，继续读取原场次",
                             "已找到 DSH 接收回执；等待原控制器写完收尾报告。",
                             source=basis(stage / "report.json"))
            return
        log = log_dir / ("resume.log" if self.process_kind == "resume" else "run.log")
        reason = (cleanup.get("Error") or report.get("CleanupError") or report.get("Error")
                  or state.get("failure"))
        if not reason:
            reason = ("单题控制器退出，发送或清理状态待核实。已保留原请求和证据。"
                      if self.process is not None else
                      RESTART_UNCONFIRMED)
        kind = "timeout" if (report.get("ObservationLimitAt") or
                             (state.get("failure") or "").endswith("inside the allowed time.")) else "error"
        source = stage / "restore" / "final.json" if cleanup.get("Error") else stage / "report.json"
        if not source.is_file():
            source = state_path if state_path.is_file() else log
        block(self.queue, job, step, reason, basis(source if source.is_file() else None), detail, kind)
        self.process = self.process_kind = None

    def result(self, job, state, report, ledger, stage):
        verification = (state.get("result") or {}).get("verification") or {}
        files = verification.get("files") or [
            {"path": f["Path"], "exists": f.get("HostMatches", False),
             "status": "missing" if f.get("Missing") else "present" if f.get("HostMatches") else "unknown"}
            for f in report.get("Outputs") or []]
        collected = sum(bool(row.get("exists") and row.get("status") != "unknown") for row in files)
        ready = read(stage / "ready.json") or {}
        config = ready.get("Configuration") or {}
        verdict = verification.get("verdict")
        if verdict == "pass":
            score = "已核对声明的内容" if job["task"]["expect"] else "内容待评"
        else:
            score = {"task_wrong_content": "交付核验未通过", "task_missing_output": "缺少声明的交付文件",
                     "collection_incomplete": "回收待核验"}.get(verdict, "内容待评")
        raw_source = stage / "report.json"
        return {"run_id": state.get("attempt_id"), "files": files, "collected": collected, "score": score,
                "formal_submissions": report.get("FormalSubmissions"),
                "turn_status": (report.get("Completion") or {}).get("TurnStatus"),
                "product_version": config.get("ProductVersion"), "model": config.get("Model"),
                "permissions": config.get("Access"), "tools": report.get("Tools"),
                "preflight": read(stage / "isolation-preflight.json"),
                "evidence": {"E2": str(raw_source)} if raw_source.is_file() else {},
                "ledger": ledger, "verification": verdict}


RESTART_UNCONFIRMED = "后台重启，缺少可自动继续或关账的凭据。已保留原请求和现场。"


def worker(queue: Queue, adapter, stop, poll):
    while not stop.is_set():
        job = None
        try:
            job, fresh = queue.claim()
            if job and (job["state"] != "blocked" or isinstance(adapter, DSH)):
                adapter.tick(job, fresh)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, sqlite3.Error, TaskLayoutError) as error:
            print(f"队列后台：{error}", file=sys.stderr, flush=True)
            if job and job["state"] != "blocked":
                block(queue, job, max((ev["stage"] for ev in queue.events(job["id"])), default=0),
                      str(error), basis(data={"错误类型": type(error).__name__, "错误": str(error)}))
        stop.wait(poll)
