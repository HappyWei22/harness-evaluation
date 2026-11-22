"""Start the loopback web interface and its independent serial worker."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import shutil
import sys
from threading import Event, Thread
from urllib.parse import parse_qs, urlsplit

from store import ACTIVITIES, HERE, Queue, TaskLayoutError, inside, now, sha256_bytes
from runner import DSH, Simulation, basis, worker
from history import ROOT as HISTORY_ROOT, replays
from run_dsh_task import control_root, ledger_path, request_store
from startup_lock import StartupLockBusy, acquire_startup_lock

WEB = HERE / "web"


def unknown(impact, check):
    return {"状态": "未知", "影响": impact, "下一步": check,
            "停止条件": "按本请求已有材料核查一次，仍缺失时保留未知。"}


class App:
    def __init__(self, queue, control, show_history):
        self.queue = queue
        self.control = control
        self.history = replays() if show_history else {}

    def summary(self, job):
        task = job["task"]
        result = job["result"]
        events = self.queue.events(job["id"], limit=3)[::-1]
        return {key: job[key] for key in ("id", "position", "product", "baseline", "state",
                                        "queued_at", "started_at", "ended_at", "reason")} | {
            "title": task["title"], "condition": task["condition"], "version": task["version"],
            "source": "模拟自测" if self.queue.mode == "simulation" else "本机 DSH",
            "activity": "自测" if self.queue.mode == "simulation" else "产品运行",
            "stage": events[0]["stage"] if events else 0, "latest": events,
            "last_event_at": events[0]["at"] if events else job["started_at"],
            "timed_out": self.queue.timed_out(job["id"]),
            "score": result.get("score", "等待结果"),
            "collected": result.get("collected"), "expected": len(task["outputs"]),
            "formal_submissions": result.get("formal_submissions"),
            "run_id": result.get("run_id") or job["id"],
        }

    @staticmethod
    def compact(record):
        return {k: v for k, v in record.items() if k not in ("events", "record", "sources", "files")}

    def job(self, id_):
        with self.queue.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (id_,)).fetchone()
            return self.queue.job(row) if row else None

    def state(self):
        jobs = self.queue.jobs()
        summaries = [self.summary(job) for job in jobs]
        history = [self.compact(run) for run in self.history.values()]
        inspection = [self.preflight(job, self.summary(job)) for job in jobs if self.is_preflight(job)]
        records = [row for row in summaries if row["state"] != "queued"] + history + inspection
        counts = {activity: sum(row["activity"] == activity and
                                (activity != "产品运行" or row.get("formal_submissions") == 1)
                                for row in records if row["source"] != "历史回放") for activity in ACTIVITIES}
        return {"jobs": summaries, "records": records, "replay": history[0] if history else None,
                "counts": counts, "options": self.queue.options(), "at": now()}

    @staticmethod
    def is_preflight(job):
        return bool((job["result"].get("preflight") or {}).get("ProbeId"))

    def preflight(self, job, summary):
        raw = job["result"]["preflight"]
        return {**summary, "id": raw["ProbeId"], "run_id": raw["ProbeId"], "title": summary["title"] + " · 隔离检查",
                "state": "closed" if raw.get("Verdict") == "pass" else "blocked",
                "activity": "接口预检", "source": "本机 DSH", "parent": job["id"],
                "score": {"pass": "预检通过", "blocked": "预检受阻"}.get(raw.get("Verdict"), "预检待核对"),
                "started_at": raw.get("StartedAt"), "ended_at": raw.get("FinishedAt"),
                "collected": None, "expected": 0, "latest": []}

    def detail(self, id_):
        if id_ in self.history:
            return self.history[id_]
        job = self.job(id_)
        if job is None:
            for parent in self.queue.jobs():
                if not self.is_preflight(parent) or parent["result"]["preflight"]["ProbeId"] != id_:
                    continue
                raw = parent["result"]["preflight"]
                source = request_store(self.control, parent["id"]) / "stage" / "isolation-preflight.json"
                return {**self.preflight(parent, self.summary(parent)), "files": [], "sources": [basis(source)],
                        "record": {"唯一ID": id_, "题目版本": raw.get("CaseVersion"),
                                   "产品版本": "不适用", "模型": "不适用", "工具": raw.get("Tools"),
                                   "权限": raw.get("Permissions"), "技能状态": "不适用",
                                   "运行时间": {"开始": raw.get("StartedAt"), "结束": raw.get("FinishedAt")},
                                   "证据等级": "E2 · 接口隔离检查过程", "最终裁定": raw.get("Verdict"),
                                   "关联请求": parent["id"]}}
            return None
        result = job["result"]
        summary = self.summary(job)
        sim = self.queue.mode == "simulation"
        task = job["task"]
        store = request_store(self.control, id_)
        sources = [
            basis(job["package"], label="冻结后的任务清单"),
            basis(Path(job["package"]).parents[2] / "原始清单.json", label="题库原始清单副本"),
        ]
        if not sim:
            stage = store / "stage"
            sources.extend(basis(path) for path in (
                store / "state.json", stage / "report.json", stage / "ready.json", stage / "go.json",
                stage / "restore" / "final.json", stage / "final-reply.txt",
                stage / "session.v4.jsonl", stage / "session.v4.jsonl.zstd",
                self.queue.root / "controller" / id_ / "run.log",
                self.queue.root / "controller" / id_ / "resume.log",
            ) if path.is_file())
        results = self.queue.root / "simulation-results" / id_ if sim else store / "stage" / "results"
        files = []
        for row in result.get("files") or []:
            entry = {**row}
            path = inside(results, row["path"])
            if row.get("exists") and row.get("status") != "unknown" and path.is_file():
                entry["download"] = self.source_key(basis(path))
                sources.append(basis(path, label=row["path"]))
            files.append(entry)
        missing_config = {
            "产品版本": unknown("版本间对比", "查看该场工作区准备回执中的可执行文件版本。"),
            "模型": unknown("模型配置复现", "查看准备回执中的模型选择与 DSH 原生会话。"),
            "工具": unknown("控制过程与产品工具的解释", "查看控制器报告和原生工具轨迹。"),
            "权限": unknown("权限可比性", "查看准备回执及原生权限事件。"),
            "技能状态": unknown("技能配置可比性", "查该场原生技能调用和配置快照。"),
        }
        fields = {"产品版本": "product_version", "模型": "model", "工具": "tools", "权限": "permissions",
                  "技能状态": "skills"}
        record = {"唯一ID": summary["run_id"], "主要活动类型": summary["activity"], "请求号": id_,
                  "研究用途": "队列与日志离线验收" if sim else "受控桌面任务执行",
                  "实际执行环境": "宿主机模拟，无虚拟机和产品调用" if sim else "Windows 宿主机与指定 DSH 基线",
                  "题目版本": {"版本": task["version"], "材料指纹": task["fingerprint"], "基线": job["baseline"]}}
        for label, field in fields.items():
            record[label] = ("不适用（模拟自测）" if sim else result.get(field) or missing_config[label])
        record.update({"运行时间": {"入队": job["queued_at"], "开场": job["started_at"], "结束": job["ended_at"]},
                       "证据等级": ("E2 · 本机队列自测过程" if sim else {
                           "E2 过程证据": "控制器回执、场次日志和账本" if result.get("evidence") else "尚无回执",
                           "E1 结果证据": "交付物和核验清单" if result.get("verification") else "尚待核验"}),
                       "最终裁定": {"流程": job["state"], "内容": summary["score"], "原因": job["reason"],
                                    "核验结论": result.get("verification"), "产品终态": result.get("turn_status"),
                                    "正式提交": 0 if sim else (result.get("formal_submissions")
                                        if result.get("formal_submissions") is not None else
                                        unknown("提交次数与现场是否应保留", "核对单次授权、原生接收和宿主账本。"))},
                       "正式比较": "不适用（模拟自测）" if sim else
                       "待按观察项核对冻结规则、证据有效、题面有效与无污染。"})
        if not sim and not task["expect"] and result.get("verification"):
            record["内容未知"] = unknown("内容正确率", "查看该题已有判卷材料及版本，按可核对的观察项裁定。")
        return {**summary, "files": files, "sources": sources, "record": record, "task": task,
                "package": job["package"]}

    def events(self, id_, after):
        if id_ in self.history:
            return [event for event in self.history[id_]["events"] if event["seq"] > after]
        return self.queue.events(id_, after=after)

    def allowed_file(self, source):
        if not source or not source.get("path"):
            return None
        path = Path(source["path"]).resolve()
        if path.is_file() and any(path.is_relative_to(root) for root in
                                  (self.queue.root, self.control / "requests", HISTORY_ROOT)):
            return path
        return None

    @staticmethod
    def source_key(source):
        return sha256_bytes(source["path"].encode("utf-8"))[:24]


class Handler(BaseHTTPRequestHandler):
    server_version = "HarnessWorkbench/1"

    @property
    def app(self):
        return self.server.app

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def file(self, path, attachment=False):
        if path is None or not path.is_file():
            self.json(404, {"error": "找不到此证据原件。"})
            return
        with path.open("rb") as fd:
            self.send_response(200)
            content = "application/octet-stream" if attachment else (mimetypes.guess_type(path)[0] or "text/plain")
            self.send_header("Content-Type", content)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; object-src 'none'; frame-ancestors 'none'")
            if attachment:
                from urllib.parse import quote
                self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + quote(path.name))
            self.end_headers()
            shutil.copyfileobj(fd, self.wfile)

    def do_GET(self):
        if not self.valid_host():
            self.json(403, {"error": "请使用本机工作台地址。"})
            return
        url = urlsplit(self.path)
        path = url.path
        if path in {"/", "/app.js", "/style.css"}:
            self.file(WEB / ("index.html" if path == "/" else path[1:]))
        elif path == "/api/state":
            self.json(200, self.app.state())
        elif path == "/api/catalog":
            self.json(200, self.app.queue.catalog())
        elif (batch := re.fullmatch(r"/api/batches/([a-f0-9-]{32,36})", path)):
            with self.app.queue.connect() as db:
                jobs = [row[0] for row in db.execute("SELECT id FROM jobs WHERE batch=? ORDER BY position",
                                                    (batch[1],))]
            self.json(200, {"jobs": jobs})
        elif (match := re.fullmatch(r"/api/runs/([\w-]+)(?:/(events|source|event-source))?", path)):
            id_, action = match.groups()
            query = parse_qs(url.query)
            if action == "events":
                self.json(200, self.app.events(id_, self.integer(query, "after", 0)))
                return
            if action == "event-source":
                seq = self.integer(query, "seq", 0)
                items = self.app.events(id_, seq-1)
                item = next((row for row in items if row["seq"] == seq), None)
                self.file(self.app.allowed_file(item["source"] if item else None), True)
                return
            run = self.app.detail(id_)
            if run is None:
                self.json(404, {"error": "找不到此场次。"})
            elif action == "source":
                key = query.get("key", [""])[0]
                source = next((item for item in run["sources"] if self.app.source_key(item) == key), None)
                self.file(self.app.allowed_file(source), True)
            else:
                body = {k: v for k, v in run.items() if k != "events"}
                body["sources"] = [{**src, "key": self.app.source_key(src),
                                     "available": self.app.allowed_file(src) is not None} for src in run["sources"]]
                self.json(200, body)
        else:
            self.json(404, {"error": "页面不存在。"})

    @staticmethod
    def integer(query, field, default):
        try:
            return int(query.get(field, [default])[0])
        except ValueError:
            return default

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != f"http://{self.headers.get('Host')}":
            self.json(403, {"error": "仅接受来自本机工作台页面的操作。"})
            return
        if self.path not in {"/api/tasks", "/api/batches"} or self.headers.get_content_type() != "application/json":
            self.json(404, {"error": "该操作需要工作台 JSON 请求。"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 128 * 1024:
                raise ValueError("表单为空或超过 128 KiB。")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("表单无效。")
            if self.path == "/api/tasks":
                self.json(200, self.app.queue.add_task(body))
                return
            jobs = self.app.queue.enqueue(body.get("id"), body.get("product"), body.get("baseline"), body.get("items"))
            self.json(200, {"jobs": jobs, "batch": body["id"]})
        except (OSError, TypeError, ValueError, TaskLayoutError) as error:
            self.json(400, {"error": str(error)})

    def log_message(self, format, *args):
        if len(args) >= 2 and str(args[1]) not in {"200", "304"}:
            super().log_message(format, *args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--mode", choices=("simulation", "dsh"), default="simulation")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--library", type=Path, action="append")
    parser.add_argument("--wait-signin", type=int, default=0)
    parser.add_argument("--no-history", action="store_true")
    args = parser.parse_args()
    control = control_root().resolve()
    if args.mode == "dsh" and not ledger_path(control).is_file():
        parser.error("真实 DSH 模式找不到宿主机运行账本：" + str(ledger_path(control)))
    libraries = args.library or ([] if args.mode == "dsh" else [HERE / "示例题库"])
    if any(not path.is_dir() for path in libraries):
        parser.error("至少一个 --library 路径不是可用的目录。")
    root = (args.state_dir or HERE / ".data" / args.mode).resolve()
    try:
        with acquire_startup_lock(root / "workbench-lock.sqlite3"):
            queue = Queue(root, libraries, args.mode)
            app = App(queue, control, not args.no_history)
            with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
                server.app = app
                stop = Event()
                adapter = Simulation(queue) if args.mode == "simulation" else DSH(queue, control, args.wait_signin)
                thread = Thread(target=worker, args=(queue, adapter, stop, 0.25 if args.mode == "simulation" else 1.2),
                                daemon=True)
                thread.start()
                print(f"评测工作台 http://127.0.0.1:{server.server_port}/  [{args.mode}]", flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
                finally:
                    stop.set()
                    thread.join()
    except (StartupLockBusy, OSError, ValueError) as error:
        parser.exit(1, f"工作台无法启动：{error}\n")


if __name__ == "__main__":
    main()
