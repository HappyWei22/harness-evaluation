// Run with Node 22+ on Windows. All HTTP traffic stays on loopback.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {randomUUID} from "node:crypto";
import {once} from "node:events";
import {cp, mkdir, readFile, writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {setTimeout as sleep} from "node:timers/promises";

const here = path.dirname(fileURLToPath(import.meta.url));
const tag = randomUUID().slice(0, 8);
const output = path.join(here, ".data", "acceptance", tag);
const edge = process.env.WORKBENCH_EDGE || path.join(process.env["ProgramFiles(x86)"] ?? "C:/Program Files (x86)",
  "Microsoft", "Edge", "Application", "msedge.exe");
const python = process.env.WORKBENCH_PYTHON || "python";
const children = new Set();
const checks = [];
const observations = {};
let browser;
let origin;

function check(name, value = true) {
  assert.ok(value, name);
  checks.push(name);
  console.log("PASS " + name);
}

async function until(callback, label, limit = 22000) {
  const end = Date.now() + limit;
  let error;
  while (Date.now() < end) {
    try {
      const result = await callback();
      if (result) return result;
    } catch (e) { error = e; }
    await sleep(130);
  }
  throw new Error(label + (error ? ": " + error.message : ""));
}

function run(program, args) {
  const child = spawn(program, args, {windowsHide: true, stdio: ["ignore","pipe","pipe"]});
  children.add(child);
  child.output = "";
  child.stdout.on("data", (chunk) => child.output += chunk.toString("utf8"));
  child.stderr.on("data", (chunk) => child.output += chunk.toString("utf8"));
  child.on("error", (error) => child.output += String(error));
  return child;
}

async function stop(child) {
  if (child.exitCode === null && child.signalCode === null && child.pid) {
    const ended = once(child, "exit");
    child.kill();
    await ended;
  }
  children.delete(child);
}

async function api(url, options) {
  const response = await fetch(origin + url, {signal: AbortSignal.timeout(4000), ...options});
  assert.ok(response.ok, url + " HTTP " + response.status + " " + (response.ok ? "" : await response.text()));
  return response.json();
}
const state = () => api("/api/state");
const events = (job, after = 0) => api("/api/runs/" + job.id + "/events?after=" + after);
const detail = (job) => api("/api/runs/" + job.id);

async function service(library, data, port = 0) {
  const child = run(python, ["-B", "-X", "utf8", path.join(here, "workbench.py"),
    "--port", String(port), "--state-dir", data, "--library", library, "--no-history"]);
  try {
    origin = await until(() => child.output.match(/http:\/\/127\.0\.0\.1:\d+/)?.[0], "服务启动");
    await state();
    return child;
  } catch (error) {
    throw new Error(error.message + "\n" + child.output);
  }
}

async function makeLibrary(label, scenario) {
  const folder = path.join(output, label, "library");
  await cp(path.join(here, "示例题库"), folder, {recursive: true});
  const manifest = path.join(folder, "A", "task.json");
  const task = JSON.parse(await readFile(manifest, "utf8"));
  task.simulation = scenario;
  await writeFile(manifest, JSON.stringify(task), "utf8");
  for (const name of ["B", "C"]) {
    const next = path.join(folder, name, "task.json");
    const task = JSON.parse(await readFile(next, "utf8"));
    task.simulation = {step_seconds:0.45};
    await writeFile(next, JSON.stringify(task), "utf8");
  }
  return folder;
}

function send(body) {
  return api("/api/batches", {method:"POST", headers: {"Content-Type":"application/json", Origin:origin},
    body: JSON.stringify(body)});
}

async function payload(folders) {
  const catalog = await api("/api/catalog");
  return {id:randomUUID(), product:"DSH", baseline:"v3", items:folders.map((folder) => {
    const {key, fingerprint} = catalog.find((row) => row.folder === folder);
    return {key, fingerprint};
  })};
}

async function connect(url) {
  const socket = new WebSocket(url);
  await once(socket, "open");
  let serial = 0;
  const pending = new Map();
  socket.addEventListener("message", ({data}) => {
    const reply = JSON.parse(data);
    const callback = pending.get(reply.id);
    if (!callback) return;
    pending.delete(reply.id);
    callback(reply);
  });
  const cdp = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++serial;
    pending.set(id, (reply) => reply.error ? reject(new Error(reply.error.message)) : resolve(reply.result));
    socket.send(JSON.stringify({id, method, params}));
  });
  const js = async (expression) => {
    const response = await cdp("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true});
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || expression);
    return response.result.value;
  };
  return {cdp, js, socket};
}

async function startBrowser() {
  const profile = path.join(output, "edge-profile");
  const process = run(edge, ["--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--disable-background-networking", "--remote-debugging-port=0", "--window-size=1440,920",
    "--user-data-dir=" + profile, "about:blank"]);
  try {
    const port = await until(async () => (await readFile(path.join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0],
      "Edge 调试端口");
    return "http://127.0.0.1:" + port.trim();
  } catch (error) {
    throw new Error(error.message + "\n" + process.output);
  }
}

async function tab(hash = "") {
  const reply = await fetch(browser + "/json/new?" + encodeURIComponent(origin + "/" + hash), {method:"PUT"});
  assert.ok(reply.ok);
  const target = await reply.json();
  return {...await connect(target.webSocketDebuggerUrl), id: target.id};
}

async function closeTab(tab) {
  tab.socket.close();
  await fetch(browser + "/json/close/" + tab.id);
}

async function click(tab, selector) {
  const query = JSON.stringify(selector);
  await until(() => tab.js('Boolean(document.querySelector(' + query + '))'), "寻找 " + selector);
  await tab.js('document.querySelector(' + query + ').click()');
}
const count = (tab, selector) => tab.js("document.querySelectorAll(" + JSON.stringify(selector) + ").length");

async function screenshot(tab, name) {
  const {data} = await tab.cdp("Page.captureScreenshot", {format:"png"});
  await writeFile(path.join(output, name), Buffer.from(data,"base64"));
}

async function serialFlow() {
  const library = await makeLibrary("serial", {step_seconds:0.85, accept_seconds:7});
  const data = path.join(output, "serial", "state");
  let server = await service(library, data);
  const port = Number(new URL(origin).port);
  let page = await tab();
  check("公开自测从空队列启动", (await state()).jobs.length === 0);
  await click(page,'[data-action="add"]');
  await click(page,'[data-task="0:B"]');
  await click(page,'[data-task="0:A"]');
  check("题目库可预览题面、输入和期望输出", await page.js(
    '["产品将收到的题面","input/sample.txt","预期交付","output/result.txt"].every(x=>document.querySelector(".preview").textContent.includes(x))'));
  await click(page,'[data-action="up"][data-value="0:A"]');
  check("选中 B、A 后可调成 A、B", await page.js(
    'Array.from(document.querySelectorAll(".order-row>span")).map(x=>x.textContent[3]).join("")==="AB"'));
  await screenshot(page, "题目库-模拟自测.png");
  const firstBody = await page.js('window.firstBody = null; const oldFetch=window.fetch; window.fetch=(u,o)=>{if(u==="/api/batches" && o?.method==="POST" && !window.firstBody){window.firstBody=JSON.parse(o.body)} return oldFetch(u,o)}; true');
  assert.ok(firstBody);
  await page.js('const add=document.querySelector(\'[data-action="submit"]\'); add.click(); add.click()');
  const initial = await until(async () => {
    const s = await state();
    return s.jobs.length === 2 && s.jobs[0].state === "running" && s.jobs;
  }, "A 工作中");
  const [a,b] = initial;
  assert.ok(a.title.startsWith("A") && b.title.startsWith("B"));
  const repeatedBody = await page.js("window.firstBody");
  assert.ok(repeatedBody);
  const replays = await Promise.all([send(repeatedBody), send(repeatedBody)]);
  check("网页连续双击及并发重试均返回同一批 A、B", replays.every((r) => r.jobs.join() === [a.id,b.id].join())
    && (await state()).jobs.length === 2);
  const bOriginal = await readFile(path.join(library,"B","input","sample.txt"),"utf8");
  await writeFile(path.join(library,"B","input","sample.txt"),"范围外改写", "utf8");
  const savedB = await detail(b);
  check("更改题库原文件后，已排 B 的副本仍为旧字节",
    await readFile(path.join(path.dirname(savedB.package),"input","sample.txt"), "utf8") === bOriginal);
  await until(() => page.js('location.hash==="#queue"'), "返回队列");
  await click(page,'[data-action="add"]');
  await click(page,'[data-task="0:C"]');
  await click(page,'[data-action="submit"]');
  const c = await until(async () => {
    const s = await state();
    return s.jobs[0].state === "running" && s.jobs[2];
  }, "A 运行时 UI 追加 C");
  check("A 运行时从网页追加 C，B 排在 C 前", (await state()).jobs.map((r) => r.id).join() === [a.id,b.id,c.id].join());

  const invoked = await until(async () => (await events(a)).find((ev) => ev.source_key === "invoke"), "等待发送控件");
  check("发送控件调用和原生接收分别记录", !(await events(a)).some((ev) => ev.source_key === "accepted"));
  await until(() => page.js('document.querySelector(".stage-message")?.textContent.includes("已调用发送控件")'), "等待确认画面");
  await screenshot(page, "队列-模拟自测.png");
  await page.js('const actualNow = Date.now; Date.now = () => actualNow() + 125000; updateWaitingClock()');
  check("等待超过两分钟只显示等待时长", await page.js(
    'document.querySelector("#waiting-clock").textContent.includes("2 分钟")&&!document.querySelector(".hero-top").textContent.includes("超时")'));
  await screenshot(page, "首页-A运行-B和C等待.png");
  await closeTab(page);
  const accepted = await until(async () => (await events(a)).find((ev) => ev.source_key === "accepted"), "关闭网页后后台收到回执");
  assert.ok(accepted.seq > invoked.seq);
  await stop(server);
  server = await service(library, data, port);
  page = await tab();
  await until(() => page.js('document.querySelector("#hero-title")?.textContent.startsWith("A")'), "重开网页看到原场次");
  const reconciled = await until(async () => (await events(a)).find((ev) => ev.source_key === "reconciled"), "原请求恢复");
  check("关闭网页后后台仍收回执，杀掉并重启后台后找回同一请求", reconciled.seq > accepted.seq);
  await click(page,'[data-action="log"]');
  await until(async () => await count(page,"#log-content [data-seq]") > 3, "打开完整日志");
  check("首页最近三条，日志保留更早事件",
    await count(page,"#latest-events li") === 3 && await count(page,"#log-content [data-seq]") > 3);
  const allDone = await until(async () => {
    const s = await state();
    return s.jobs.every((j) => j.state === "closed") && s;
  }, "ABC 三场关账", 45000);
  check("同一日志面板在后场开始后仍显示 A", await page.js(
    'document.querySelector("#log-dialog").open && document.querySelector("#log-heading").textContent.startsWith("A")'));
  await page.cdp("Input.dispatchKeyEvent", {type:"keyDown",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
  await page.cdp("Input.dispatchKeyEvent", {type:"keyUp",key:"Escape",code:"Escape",windowsVirtualKeyCode:27});
  await until(() => page.js('!document.querySelector("#log-dialog").open'), "Escape 关闭日志");
  await click(page,'[data-action="stage"][data-value="3"]');
  await until(async () => await count(page,"#log-content [data-seq]") > 0, "点击回收阶段");
  check("进度节点直接筛到回收；切回全部可补全", await page.js(
    'document.querySelector("#log-stage").value==="3" && document.querySelector("#log-content").textContent.includes("回收文件") && !document.querySelector("#log-content").textContent.includes("产品已收到题面")'));
  await page.js('const picker=document.querySelector("#log-stage"); picker.value="all";picker.dispatchEvent(new Event("change",{bubbles:true}));true');
  assert.ok(await count(page,"#log-content [data-seq]") > 3);
  await click(page,'#log-content details summary');
  check("日志条目可展开原始依据", await count(page,"#log-content details[open] .raw") === 1);
  await page.js('document.querySelector("#log-dialog").close()');
  await click(page,'nav [data-page="records"]');
  await until(() => page.js('document.querySelector(".record-table")?.textContent.includes("A ·")'), "运行记录中找回 A");
  await click(page,'[data-action="record"][data-value="'+a.id+'"]');
  await until(() => page.js('document.querySelector("#detail-content").textContent.includes("展开运行信息与裁定")'), "场次明细");
  await screenshot(page, "运行记录-模拟自测.png");
  check("三场写入自测记录，新增产品运行数为零", allDone.counts["自测"] === 3 && allDone.counts["产品运行"] === 0);
  check("A、B、C 按序开场且均晚于上一场关账",
    allDone.jobs.map((r) => r.id).join() === [a.id,b.id,c.id].join() &&
    allDone.jobs.slice(1).every((r, i) => Date.parse(r.started_at) > Date.parse(allDone.jobs[i].ended_at)));
  const logs = {};
  for (const job of allDone.jobs) {
    const history = await events(job);
    logs[job.id] = history;
    assert.deepEqual(history.map((e) => e.seq), history.map((e, i) => i+1));
    assert.equal(history.filter((e) => e.source_key === "invoke").length, 1);
    assert.equal(history.filter((e) => e.source_key === "accepted").length, 1);
    assert.deepEqual(await events(job, 3), history.slice(3));
    assert.deepEqual(history.slice(-5).map((row) => row.source_key),
      ["restore_requested","restored","booted","verified","closed"]);
  }
  check("三场各只发一次模拟题面，日志序号连续且增量一致，末场也完成恢复核验和关账");
  await page.js('document.querySelector("#detail-dialog").close()');
  await click(page,'nav [data-page="queue"]');
  await page.cdp("Emulation.setDeviceMetricsOverride", {width:720,height:460,deviceScaleFactor:2,mobile:false});
  check("1440 宽屏按 200% 等效视口布局无页面横向溢出", await page.js(
    'document.documentElement.scrollWidth<=innerWidth && getComputedStyle(document.querySelector(".sidebar")).position==="static"'));
  await screenshot(page, "首页-200百分比等效视口.png");
  observations.serial = {batch: repeatedBody.id, jobs: allDone.jobs, events: logs};
  await closeTab(page);
  await stop(server);
}

async function faultFlow(label, scenario) {
  const library = await makeLibrary(label, {step_seconds:0.25, ...scenario});
  const data = path.join(output, label, "state");
  let server = await service(library, data);
  const port = Number(new URL(origin).port);
  const batch = await send(await payload(["A","B","C"]));
  if (label === "uncertain") {
    await until(async () => (await events({id:batch.jobs[0]})).find((row) => row.source_key === "invoke"), "记录调用");
    await stop(server);
    server = await service(library, data, port);
  }
  const snapshot = await until(async () => {
    const s = await state();
    return s.jobs[0].state === "blocked" && s;
  }, label + " 暂停");
  assert.deepEqual(snapshot.jobs.map((j) => j.state), ["blocked","queued","queued"]);
  const log = await events(snapshot.jobs[0]);
  const last = log.at(-1);
  assert.ok(last.source?.data && last.kind !== "info");
  assert.equal(log.filter((e) => e.source_key === "invoke").length, 1);
  assert.ok(!log.some((row) => row.source_key === "closed"));
  if (label === "restore") {
    assert.equal(last.stage, 4);
    assert.equal(last.source.data.Verdict, "fail");
    check("模拟虚拟机恢复失败留存原因和报告，B、C 继续等待");
    const page = await tab();
    await until(() => page.js('document.querySelector(".blocked")?.textContent.includes("Hyper-V")'), "恢复失败的页面原因");
    check("首页在恢复节点标暂停，并保留待运行队列", await page.js(
      'document.querySelector(\'[aria-label="恢复虚拟机，已暂停"]\')!==null && document.querySelectorAll("tbody tr").length===2'));
    await screenshot(page, "首页-模拟恢复失败.png");
    await closeTab(page);
  } else {
    assert.ok(!log.some((row) => row.source_key === "accepted"));
    if (label === "timeout") {
      assert.ok(snapshot.jobs[0].timed_out && log.some((row) => row.source_key === "timeout"));
      check("后台明确报告超时后才标超时，B、C 继续等待");
    } else check("发送后缺少回执时重启，保留现场并暂停，没有再次调用发送");
  }
  observations[label] = {jobs:snapshot.jobs, events:log};
  await stop(server);
}

await mkdir(output, {recursive:true});
const testStarted = new Date().toISOString();
function testEvidence(verdict) {
  return {id:"WB-OFFLINE-"+tag, activity:"自测", startedAt:testStarted, endedAt:new Date().toISOString(),
    environment:"Windows 本机、Edge 和模拟后台", productVersion:"不适用", model:"不适用",
    tools:"Node CDP、Windows Edge、Python 本机服务", permissions:"本机环回和测试目录",
    skills:"不适用", taskVersion:"offline-1", evidenceLevel:"E2 · 本机工作台自测过程",
    formalSubmissions:0, verdict, checks, observations};
}
try {
  browser = await startBrowser();
  await serialFlow();
  await faultFlow("restore", {fault:"restore"});
  await faultFlow("uncertain", {accept_seconds:15});
  await faultFlow("timeout", {fault:"timeout",accept_seconds:1});
  await writeFile(path.join(output, "验收结果.json"), JSON.stringify(testEvidence("pass"), null, 2), "utf8");
  console.log("验收结果 " + output);
} catch (error) {
  await writeFile(path.join(output, "未通过.json"),
    JSON.stringify({...testEvidence("验收未完成"),error:String(error)},null,2),"utf8");
  throw error;
} finally {
  await Promise.allSettled([...children].reverse().map(stop));
}
