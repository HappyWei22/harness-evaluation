"""Local queue, immutable task packages and durable run events."""

from contextlib import contextmanager
from ipaddress import IPv4Address
import json
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
GATE = HERE.parent / "windows-single-task"
sys.path.insert(0, str(GATE))
from dsh_checkpoints import DSH_CHECKPOINTS, DEFAULT_DSH_BASELINE
from task_request import TaskLayoutError, load_manifest, now, sha256_bytes, sha256_file

STAGES = ("准备环境", "提交题面", "产品处理", "回收文件", "恢复虚拟机", "关账")
ACTIVITIES = ("产品运行", "自测", "离线复算", "判卷回归", "裸模型探针", "接口预检")
ART04 = "dsh-art04-footer-v2"
SCORING_PATH_PLACEHOLDER = "<评分文件路径>"


def read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def default_switch_ipv4():
    script = (
        "[Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | "
        "Where-Object Name -eq 'vEthernet (Default Switch)' | "
        "ForEach-Object { $_.GetIPProperties().UnicastAddresses } | "
        "Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' -and "
        "$_.DuplicateAddressDetectionState -eq 'Preferred' } | "
        "ForEach-Object { $_.Address.IPAddressToString }"
    )
    try:
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout=8, text=True, stderr=subprocess.DEVNULL,
        )
        return IPv4Address(output.strip())
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise TaskLayoutError("无法确认默认交换机上唯一可用的宿主机 IPv4 地址。") from error


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise TaskLayoutError(f"文件在声明的目录之外：{relative}")
    return path


def inspect_package(path):
    raw = path.read_bytes()
    task = load_manifest(path)
    if path.read_bytes() != raw:
        raise TaskLayoutError("清单在读取期间发生变化，请重新预览。")
    files = {}
    for relative in task["inputs"]:
        source = inside(path.parent, relative)
        if not source.is_file():
            raise TaskLayoutError(f"缺少输入文件 {relative}")
        files[relative] = source.read_bytes()
    if task["version"] == ART04:
        lines = task["prompt"].splitlines()
        if len(lines) < 2 or lines[0] != "本次工作目录是：" or not (
            lines[1] == "{{WORKSPACE}}" or re.fullmatch(
                r"C:\\Users\\alex_\\AppData\\Local\\Temp\\[\w-]+\\WS-[\w-]+", lines[1]
            )
        ):
            raise TaskLayoutError("页脚实验题面第二行须为工作区占位符 {{WORKSPACE}} 或该题原有的来宾路径。")
        canary = path.parents[2] / "评分侧样本.txt"
        if not canary.is_file():
            raise TaskLayoutError("缺少页脚实验的宿主机隔离检查文件 评分侧样本.txt（题包上两级）。")
        files["评分侧样本.txt"] = canary.read_bytes()
    fingerprint = {"task.json": sha256_bytes(raw),
                   **{name: sha256_bytes(data) for name, data in files.items()}}
    return task, raw, files, sha256_bytes(encoded(fingerprint).encode("utf-8"))


def check_material_path(name):
    if not isinstance(name, str):
        raise ValueError("材料路径须为文本。")
    parts = name.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) or
           re.search(r'[\x00-\x1f<>:"|?*]', part) or Path(part).is_reserved()
           for part in parts):
        raise ValueError(f"材料路径须为有效的工作区相对路径：{name!r}")
    if parts[0].casefold() == "task.json":
        raise ValueError("task.json 只用于宿主机清单。")


class Queue:
    def __init__(self, root, libraries, mode):
        self.root = root.resolve()
        self.imported = self.root / "题库"
        self.libraries = (*tuple(path.resolve() for path in libraries), self.imported)
        self.mode = mode
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "queue.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, signature TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    position INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                    batch TEXT NOT NULL REFERENCES batches(id), product TEXT NOT NULL,
                    baseline TEXT NOT NULL, task TEXT NOT NULL, package TEXT NOT NULL,
                    state TEXT NOT NULL, queued_at TEXT NOT NULL, started_at TEXT,
                    ended_at TEXT, reason TEXT, result TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS serial_active ON jobs ((1))
                WHERE state IN ('running', 'blocked');
                CREATE TABLE IF NOT EXISTS events (
                    job TEXT NOT NULL REFERENCES jobs(id), seq INTEGER NOT NULL,
                    source_key TEXT NOT NULL, stage INTEGER NOT NULL, at TEXT NOT NULL,
                    action TEXT NOT NULL, result TEXT NOT NULL, source TEXT,
                    kind TEXT NOT NULL, PRIMARY KEY(job, seq), UNIQUE(job, source_key)
                );
            """)
            db.execute("INSERT OR IGNORE INTO settings VALUES ('mode', ?)", (mode,))
            if db.execute("SELECT value FROM settings WHERE key='mode'").fetchone()[0] != mode:
                raise ValueError("该数据目录属于另一种运行模式。")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def task_path(self, key):
        index, relative = key.split(":", 1)
        if not index.isdecimal() or int(index) >= len(self.libraries):
            raise ValueError("题目目录不在已配置的题库中。")
        return inside(self.libraries[int(index)], Path(relative) / "task.json")

    def add_task(self, body):
        """Publish a verified local copy. Adding a task never starts a run."""
        source = Path(str(body.get("directory") or "").strip().strip('"'))
        if not source.is_absolute() or not source.is_dir():
            raise ValueError("请输入宿主机上存在的题目目录完整路径。")
        source = source.resolve()
        manifest = body.get("manifest")
        filename = "task.json"
        if manifest is None:
            path = source / "task.json"
            if not path.is_file():
                path = source / "task.template.json"
                if not path.is_file():
                    raise ValueError("目录中缺少 task.json 或 task.template.json，可选择「填写题面」添加这道题。")
                filename = path.name
            raw = path.read_bytes()
            manifest = json.loads(raw.decode("utf-8-sig"))
            raw = raw.removeprefix(b'\xef\xbb\xbf')
        else:
            raw = encoded(manifest).encode("utf-8")
        if not isinstance(manifest, dict):
            raise ValueError("题目清单须为 JSON 对象。")
        for field in ("inputs", "outputs"):
            names = manifest.get(field, [])
            if not isinstance(names, list):
                raise ValueError(f"{field} 须为文件列表。")
            for name in names:
                check_material_path(name)
            if len({name.replace("\\", "/").casefold() for name in names}) != len(names):
                raise ValueError(f"{field} 中有重复路径。")
        with TemporaryDirectory(dir=self.root) as tmp:
            bundle = Path(tmp) / "材料"
            folder = bundle / "题包" / (source.name or "task")
            folder.mkdir(parents=True)
            path = folder / filename
            path.write_bytes(raw)
            task = load_manifest(path)
            for name in task["inputs"]:
                original = inside(source, name)
                if not original.is_file():
                    raise ValueError(f"找不到输入文件：{name}")
                dest = inside(folder, name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, dest)
            if task["version"] == ART04:
                canary = source.parent.parent / "评分侧样本.txt"
                if not canary.is_file():
                    raise ValueError("此页脚题包缺少对应的宿主机评分侧样本。")
                shutil.copyfile(canary, bundle / canary.name)
            _, _, _, fingerprint = inspect_package(path)
            self.imported.mkdir(exist_ok=True)
            target = self.imported / (("template-" if filename != "task.json" else "") + fingerprint)
            relative = path.relative_to(bundle)
            try:
                bundle.rename(target)
            except OSError:
                if not target.is_dir():
                    raise
                existing = next((target / "题包").glob("*/" + filename), None)
                if existing is None or inspect_package(existing)[3] != fingerprint:
                    raise ValueError("已保存的题包与本次材料不一致，请核查题库副本。")
                relative = existing.relative_to(target)
        return {"key": f"{len(self.libraries)-1}:{(target / relative).parent.relative_to(self.imported).as_posix()}"}

    def catalog(self):
        rows = []
        for index, root in enumerate(self.libraries):
            paths = list(root.rglob("task.json"))
            if root == self.imported:
                paths.extend(root.rglob("task.template.json"))
            for path in sorted(paths):
                relative = path.parent.relative_to(root).as_posix()
                key = f"{index}:{relative}"
                try:
                    task, _, files, fingerprint = inspect_package(inside(root, path.relative_to(root)))
                    rows.append(dict(key=key, folder=path.parent.name if root == self.imported else relative,
                                     title=task.get("title") or task["task_id"],
                                     template=path.name == "task.template.json",
                                     version=task["version"], condition=task.get("condition") or
                                     (path.parent.name if task["version"] == ART04 else ""),
                                     prompt=task["prompt"], fingerprint=fingerprint,
                                     inputs=[dict(path=name, bytes=len(files[name])) for name in task["inputs"]],
                                     outputs=task["outputs"], expect=task["expect"]))
                except (OSError, ValueError, TaskLayoutError) as error:
                    rows.append(dict(key=key, folder=relative, title=path.parent.name, error=str(error)))
        return rows

    def freeze(self, id_, key, expected_fingerprint):
        path = self.task_path(key)
        if not path.exists() and path.with_name("task.template.json").is_file():
            raise ValueError("该题为尚待准备的模板。请添加已准备的本场题包后再运行。")
        task, raw, files, fingerprint = inspect_package(path)
        if fingerprint != expected_fingerprint:
            raise ValueError("题目材料在预览后有变化，请刷新题目库后重新选择。")
        folder = self.root / "packages" / id_
        task_root = folder / "题包" / id_
        task_root.mkdir(parents=True, exist_ok=False)
        (folder / "原始清单.json").write_bytes(raw)
        for name in task["inputs"]:
            dest = inside(task_root, name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(files[name])
        manifest = json.loads(raw)
        manifest_path = task_root / "task.json"
        if task["version"] == ART04:
            from run_dsh_task import guest_workspace
            (folder / "评分侧样本.txt").write_bytes(files["评分侧样本.txt"])
            lines = task["prompt"].splitlines(keepends=True)
            lines[1] = guest_workspace(id_) + lines[1][len(lines[1].rstrip("\r\n")):]
            manifest["prompt"] = "".join(lines)
        elif self.mode == "dsh" and (task["task_id"], task["version"]) == ("issue3-minimal", "1"):
            if manifest_path.drive.upper() != "E:" or task["prompt"].count(SCORING_PATH_PLACEHOLDER) != 1:
                raise TaskLayoutError("库存汇总题需要 E: 盘题包和一个评分文件路径占位符。")
            path_on_share = str(manifest_path)[2:]
            unc = rf"\\{default_switch_ipv4()}\E$" + path_on_share
            manifest["prompt"] = task["prompt"].replace(SCORING_PATH_PLACEHOLDER, unc)
        rewritten = task["version"] == ART04 or manifest["prompt"] != task["prompt"]
        manifest_path.write_bytes(encoded(manifest).encode("utf-8") if rewritten else raw)
        pinned = {name: sha256_bytes(files[name]) for name in task["inputs"]}
        pinned["task.json"] = sha256_file(manifest_path)
        metadata = dict(title=task.get("title") or task["task_id"], version=task["version"],
                        task_id=task["task_id"], condition=task.get("condition") or
                        (self.task_path(key).parent.name if task["version"] == ART04 else ""),
                        prompt=manifest["prompt"], inputs=task["inputs"], outputs=task["outputs"],
                        expect=task["expect"], fingerprint=fingerprint, hashes=pinned,
                        scenario=task.get("simulation") if self.mode == "simulation" else None)
        if task["version"] == ART04:
            metadata["canary_hash"] = sha256_bytes(files["评分侧样本.txt"])
        return metadata, manifest_path

    def enqueue(self, batch, product, baseline, items):
        if not isinstance(batch, str) or not re.fullmatch(r"[a-f0-9-]{32,36}", batch):
            raise ValueError("入队凭据无效。")
        if product != "DSH" or baseline not in DSH_CHECKPOINTS:
            raise ValueError("请选择已登记的 DSH 基线。")
        if not isinstance(items, list) or not 1 <= len(items) <= 100 or any(
            not isinstance(i, dict) or not isinstance(i.get("key"), str)
            or not isinstance(i.get("fingerprint"), str) for i in items
        ):
            raise ValueError("请选择 1 至 100 个可用题目。")
        if len({i["key"] for i in items}) != len(items):
            raise ValueError("本批包含重复题目。")
        signature = encoded([product, baseline, items])
        created = []
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                prior = db.execute("SELECT signature FROM batches WHERE id=?", (batch,)).fetchone()
                if prior:
                    if prior["signature"] != signature:
                        raise ValueError("该入队凭据已用于另一批题目。")
                    return [row[0] for row in db.execute(
                        "SELECT id FROM jobs WHERE batch=? ORDER BY position", (batch,))]
                at = now()
                db.execute("INSERT INTO batches VALUES (?, ?, ?)", (batch, signature, at))
                for item in items:
                    id_ = ("sim-" if self.mode == "simulation" else "wb-") + uuid4().hex[:24]
                    created.append(id_)
                    task, copy = self.freeze(id_, item["key"], item["fingerprint"])
                    if task["version"] == ART04:
                        if self.mode == "dsh":
                            raise ValueError("本发布版通过标准单题入口运行；请在原专项环境执行 ART04 页脚实验。")
                        if baseline != "v3":
                            raise ValueError("三文件修订与多轮交付（页脚实验）要求使用 v3 基线。")
                    db.execute("""INSERT INTO jobs
                        (id,batch,product,baseline,task,package,state,queued_at)
                        VALUES (?,?,?,?,?,?,'queued',?)""",
                        (id_, batch, product, baseline, encoded(task), str(copy), at))
                return created
        except Exception:
            for id_ in created:
                shutil.rmtree(self.root / "packages" / id_, ignore_errors=True)
            raise

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE state IN ('running','blocked')").fetchone()
            if row:
                return self.job(row), False
            row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY position LIMIT 1").fetchone()
            if row is None:
                return None, False
            at = now()
            db.execute("UPDATE jobs SET state='running', started_at=? WHERE id=?", (at, row["id"]))
            job = self.job(row)
            return {**job, "state": "running", "started_at": at}, True

    @staticmethod
    def job(row):
        return {**dict(row), "task": json.loads(row["task"]),
                "result": json.loads(row["result"]) if row["result"] else {}}

    def jobs(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY position").fetchall()
            return [self.job(row) for row in rows]

    def update(self, id_, state, result=None, reason=None):
        with self.connect() as db:
            db.execute("""UPDATE jobs SET state=?, result=COALESCE(?, result), reason=?,
                ended_at=CASE WHEN ?='closed' THEN COALESCE(ended_at, ?) ELSE ended_at END
                WHERE id=? AND state IN ('running','blocked')""",
                (state, encoded(result) if result is not None else None, reason, state, now(), id_))

    def event(self, id_, key, stage, action, result, *, at=None, source=None, kind="info"):
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO events
                SELECT ?,COALESCE(MAX(seq),0)+1,?,?,?,?,?,?,? FROM events WHERE job=?""",
                (id_, key, stage, at or now(), action, result, encoded(source) if source else None, kind, id_))

    def events(self, id_, after=0, limit=None):
        with self.connect() as db:
            sql = "SELECT * FROM events WHERE job=? AND seq>?"
            params = (id_, after)
            if limit:
                sql += " ORDER BY julianday(at) DESC, seq DESC LIMIT ?"
                params += (limit,)
            else:
                sql += " ORDER BY seq"
            rows = [dict(row) for row in db.execute(sql, params)]
        if limit:
            rows.reverse()
        for row in rows:
            row["source"] = json.loads(row["source"]) if row["source"] else None
        return rows

    def timed_out(self, id_):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM events WHERE job=? AND kind='timeout' LIMIT 1", (id_,)).fetchone() is not None

    def verify(self, job):
        path = Path(job["package"])
        for name, expected in job["task"]["hashes"].items():
            if sha256_file(inside(path.parent, name)) != expected:
                raise ValueError(f"题包副本校验失败：{name}")
        if "canary_hash" in job["task"]:
            if sha256_file(path.parents[2] / "评分侧样本.txt") != job["task"]["canary_hash"]:
                raise ValueError("页脚实验隔离检查文件校验失败。")

    def options(self):
        return {"mode": self.mode, "checkpoints": {k: name for k, (name, _) in DSH_CHECKPOINTS.items()},
                "baseline": DEFAULT_DSH_BASELINE, "libraries": [str(path) for path in self.libraries]}
