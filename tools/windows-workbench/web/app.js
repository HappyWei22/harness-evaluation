"use strict";

const STAGES = ["准备环境", "提交题面", "产品处理", "回收文件", "恢复虚拟机", "关账"];
const ACTIVITIES = ["产品运行", "自测", "离线复算", "判卷回归", "裸模型探针", "接口预检"];
const LABELS = {queue: "运行队列", library: "题目库", records: "运行记录"};
const FLOW = {queued: "等待运行", running: "运行中", closed: "已关账", blocked: "已暂停"};
const $ = (selector) => document.querySelector(selector);
const view = $("#view");
const logDialog = $("#log-dialog");
const detailDialog = $("#detail-dialog");
const timeFormat = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai",
  hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23"});
const dateFormat = new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai",
  month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"});

let state, catalog, catalogError, previewKey, baseline, log, noticeTimer;
let selected = [];
let page = "queue";
let filter = "全部";
let connected = false;
let refreshing = false;
let submitting = false;
let pendingReady = false;
let renderKey = "";
let detailTarget, logTarget;

function escape(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) =>
    ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[char]));
}

function stamp(value, formatter = timeFormat) {
  return value && Number.isFinite(Date.parse(value)) ? formatter.format(new Date(value)) : "—";
}

function pretty(value) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

async function request(path, options) {
  const response = await fetch(path, {cache: "no-store", signal: AbortSignal.timeout(10000), ...options});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error(data.error || "本机工作台请求失败");
    error.confirmedFailure = response.status >= 400 && response.status < 500;
    throw error;
  }
  return data;
}

function toast(message) {
  $("#notice").textContent = message;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => $("#notice").textContent = "", 5200);
}

function pageHeader(title, subtitle, actions = "") {
  return `<header class="topbar"><div><h1>${title}</h1><p>${subtitle}</p></div>
    <div class="top-actions">${actions}</div></header>`;
}

function chip(text, style = "") {
  return `<span class="chip ${style}">${escape(text)}</span>`;
}

function sourceChip(source) {
  return chip(source, source === "历史回放" ? "replay" : source === "模拟自测" ? "warning" : "active");
}

function button(text, action, value = "", style = "ghost", attributes = "") {
  return `<button type="button" class="btn ${style}" data-action="${action}" data-value="${escape(value)}"
    data-focus="${action}:${escape(value)}" ${attributes}>${text}</button>`;
}

function filesLabel(run) {
  if (!run.expected) return "—";
  if (run.collected === null || run.collected === undefined) return `待回收 · 预期 ${run.expected} 件`;
  if (run.collected === run.expected) return `${run.collected} 件文件已收齐`;
  return `已回收 ${run.collected} / ${run.expected} 件`;
}

function timeEvent(event, full = false) {
  const source = event.source;
  const url = `/api/runs/${encodeURIComponent(event.job)}/event-source?seq=${event.seq}`;
  const evidence = source ? `<details data-evidence="${event.seq}"><summary>查看原始依据 · ${escape(source.label)}</summary>
    ${source.data ? `<pre class="raw">${escape(pretty(source.data))}</pre>` : ""}
    ${source.path ? `<a href="${url}">下载原始记录</a><p>${escape(source.path)}</p>` : ""}</details>` : "";
  const action = !full && source ? `<button type="button" class="event-action" data-action="event-log"
    data-value="${event.seq}" data-focus="event-log:${event.seq}" aria-label="查看原始依据 · ${escape(event.action)}">
    ${escape(event.action)}</button>` : `<strong>${escape(event.action)}</strong>`;
  return `<li data-seq="${event.seq}" class="${escape(event.kind)}"><time datetime="${escape(event.at)}">${stamp(event.at)}</time>
    <div>${full ? `<div class="small muted">#${event.seq} · ${STAGES[event.stage]}</div>` : ""}
    ${action}<p>${escape(event.result)}</p>${full ? evidence : ""}</div></li>`;
}

function queuePage() {
  const current = state.jobs.find((run) => ["running", "blocked"].includes(run.state));
  const waiting = state.jobs.filter((run) => run.state === "queued");
  const last = state.jobs.filter((run) => run.state === "closed").at(-1);
  const run = current || last || state.replay;
  const status = current ? (current.state === "blocked" ? "1 场暂停" : "1 场运行中") : "当前空闲";
  const add = button('<span aria-hidden="true">＋</span> 添加到队列', "add", "", "primary");
  const top = pageHeader("运行队列", `${status} · ${waiting.length} 场等待中`, add);
  const rows = waiting.map((item, index) => `<tr><td class="numeric">${String(index+1).padStart(2,"0")}</td>
    <td><strong>${escape(item.title)}</strong><span class="sub">${escape(item.condition || `版本 ${item.version}`)}</span></td>
    <td>DSH <span class="sub">${escape(item.baseline)} 基线</span></td>
    <td>${button("题包", "record", item.id, "ghost", `aria-label="查看 ${escape(item.title)} 的冻结题包"`)}</td></tr>`).join("");
  const queue = `<section aria-labelledby="waiting-title"><div class="section-head"><h2 id="waiting-title">等待运行</h2>
    <p>按入队顺序</p></div><div class="card table-wrap">${waiting.length ? `<table><thead>
    <tr><th scope="col">顺序</th><th scope="col">题目 / 条件</th><th scope="col">产品</th><th scope="col">副本</th></tr></thead>
    <tbody>${rows}</tbody></table>` : `<div class="empty"><strong>当前没有待运行任务</strong>
    <p>从题目库勾选目录，安排下一批场次。</p></div>`}</div></section>`;
  let hero = `<section class="card hero"><div class="overline">当前运行</div><h2>暂无场次</h2>
    <p class="muted">入队后，后台会自动领取第一场。</p></section>`;
  let latest = `<div class="card empty"><p>场次开跑后，新的后台事件会出现在这里。</p></div>`;
  let viewLog = "";
  if (run) {
    const steps = STAGES.map((name, index) => {
      const done = run.state === "closed" || index < run.stage;
      const active = run.state !== "closed" && index === run.stage;
      const style = done ? "done" : active ? (run.state === "blocked" ? "error" : "active") : "";
      const marker = done ? "✓" : style === "error" ? "!" : "";
      const label = done ? "，已完成" : active ? (style === "error" ? "，已暂停" : "，当前阶段") : "，等待";
      return `<li class="${style}"><button class="step" type="button" data-action="stage" data-value="${index}"
        data-focus="stage:${index}" aria-label="${name}${label}" ${active ? 'aria-current="step"' : ""}>
        <i aria-hidden="true">${marker}</i><span>${name}</span></button></li>`;
    }).join("");
    const stageLabel = run.state === "closed" ? "已关账" : run.state === "blocked" ?
      (run.timed_out ? "已暂停 · 超时" : "已暂停") :
      run.timed_out ? "已达观察时限" : `正在${STAGES[run.stage]}`;
    const style = run.state === "closed" ? "success" : run.state === "blocked" ? "error" : "active";
    const evidenceHint = run.latest[0];
    const message = run.state === "blocked" ? `<p class="stage-message blocked">${escape(run.reason)} · 待排场次继续等待</p>` :
      run.state === "running" ? `<p class="stage-message">${escape(evidenceHint ? evidenceHint.action + " · " + evidenceHint.result : "等待宿主机第一条记录")}
        <span id="waiting-clock"></span></p>` : "";
    hero = `<section class="card hero" aria-labelledby="hero-title"><div class="hero-top"><div>
      <div class="overline">${current ? "当前场次" : run.source === "历史回放" ? "历史场次" : "最近完成"}</div>
      <h2 id="hero-title">${escape(run.title)}</h2><div class="meta"><span>DSH</span>
      ${run.condition ? `<span>${escape(run.condition)}</span>` : ""}<span>${escape(run.baseline)} 基线</span><span>Windows 11</span></div></div>
      <div class="chips">${sourceChip(run.source)}${chip(stageLabel, style)}</div></div>
      <ol class="steps" aria-label="六步运行流程">${steps}</ol>${message}
      <div class="outcomes"><span>交付情况 <strong>${filesLabel(run)}</strong></span>
      <span>产品结果 <strong>${escape(run.score)}</strong></span>
      ${button("场次详情", "record", run.id)}</div></section>`;
    latest = `<div class="card">${run.latest.length ?
      `<ol class="timeline" id="latest-events">${run.latest.map((event) => timeEvent(event)).join("")}</ol>` :
      '<div class="empty">等待第一条后台记录。</div>'}</div>`;
    viewLog = button("查看完整日志", "log", run.id);
  }
  return `${top}${hero}<div class="workspace-grid">${queue}<section aria-labelledby="events-title">
    <div class="section-head"><h2 id="events-title">${current ? "当前场次动态" : "场次动态"}</h2>${viewLog}</div>${latest}</section></div>`;
}

function libraryPage() {
  const summary = state.options.mode === "simulation" ? "模拟模式 · 题目只进入本机队列自测" : "DSH 正式模式 · 由本机单题入口执行";
  const top = pageHeader("题目库", summary, button("刷新题库", "refresh-library") + button("添加本机题目", "new-task", "", "primary"));
  if (!catalog) return top + `<div class="card empty">${escape(catalogError || "正在读取题库…")}</div>`;
  const task = catalog.find((item) => item.key === previewKey) || catalog[0];
  const tasks = catalog.map((item) => `<li class="task-row">
    <input type="checkbox" aria-label="选择 ${escape(item.title)}" data-task="${escape(item.key)}"
      data-focus="check:${escape(item.key)}" ${selected.includes(item.key) ? "checked" : ""} ${item.template || item.error || submitting ? "disabled" : ""}>
    <button type="button" class="task-link" data-action="preview" data-value="${escape(item.key)}"
      data-focus="preview:${escape(item.key)}" aria-current="${item === task}"><strong>${escape(item.title)}</strong> ${item.template ? chip("模板 · 待准备", "warning") : ""}
      <span class="path">${escape(item.folder)}${item.version ? ` · 版本 ${escape(item.version)}` : ""}</span>
      ${item.error ? `<span class="path">${escape(item.error)}</span>` : ""}</button></li>`).join("");
  const preview = !task ? '<div class="empty">配置的题库中没有找到 task.json。</div>' : task.error ?
    `<h2>${escape(task.title)}</h2><div class="banner">${escape(task.error)}</div>` :
    `<h2>${escape(task.title)}</h2><p class="path">${escape(task.folder)} · 版本 ${escape(task.version)}</p>
    ${task.template ? '<p class="banner">已保留原始模板。请在准备好本场的 task.json 后添加该题包。</p>' : ""}
    <h3>${task.template ? "题面模板" : "产品将收到的题面"}</h3>${task.version === "dsh-art04-footer-v2" ?
      '<p class="small muted">入队时会把第二行替换为本场独立的虚拟机工作区路径。</p>' : ""}
    ${!task.template && state.options.mode === "dsh" && task.prompt.includes("<评分文件路径>") ?
      '<p class="small muted">入队时会填入本场宿主机评分文件的实际路径。</p>' : ""}
    <pre tabindex="0">${escape(task.prompt)}</pre><h3>输入文件</h3>
    ${task.inputs.length ? `<ul class="files-list">${task.inputs.map((input) =>
      `<li>${escape(input.path)} <span class="muted small">· ${input.bytes.toLocaleString("zh-CN")} 字节</span></li>`).join("")}</ul>` :
      '<p class="muted">本题没有声明输入文件。</p>'}
    <h3>预期交付（宿主机核验）</h3><ul class="files-list">${task.outputs.map((output) =>
      `<li>${escape(output)}${Object.hasOwn(task.expect, output) ? `<pre>${escape(task.expect[output])}</pre>` : ""}</li>`).join("")}</ul>`;
  const ordered = selected.map((id, index) => {
    const row = catalog.find((item) => item.key === id);
    return `<li class="order-row"><span>${index+1}. ${escape(row.title)}</span><div>
      ${button("上移", "up", id, "ghost", `aria-label="上移 ${escape(row.title)}" ${index === 0 || submitting ? "disabled" : ""}`)}
      ${button("下移", "down", id, "ghost", `aria-label="下移 ${escape(row.title)}" ${index === selected.length-1 || submitting ? "disabled" : ""}`)}
    </div></li>`;
  }).join("");
  const checkpoints = Object.entries(state.options.checkpoints).map(([id, name]) =>
    `<option value="${escape(id)}" ${baseline === id ? "selected" : ""}>${escape(name)}</option>`).join("");
  const pending = pendingBatches();
  return `${top}${catalogError ? `<div class="banner">${escape(catalogError)}</div>` : ""}
    ${pending.length ? `<div class="banner">有 ${pending.length} 次入队的回执待确认。
      ${button("核对并重试原请求", "retry", "", "ghost", submitting ? "disabled" : "")}</div>` : ""}
    <div class="split"><section class="card library-card"><h2>已配置的题目目录</h2>
      ${tasks.length ? `<ul class="list">${tasks}</ul>` : '<p class="muted">题库为空。</p>'}
      <details><summary>查看题库位置</summary><pre>${escape(state.options.libraries.join("\n"))}</pre></details></section>
      <section class="card preview">${preview}</section></div>
    <section class="card batch-card"><h2>本批入队顺序</h2>
    ${ordered ? `<ol class="list">${ordered}</ol>` : '<p class="muted">勾选题目后可在这里调整顺序。</p>'}
    <div class="form-actions"><label>产品<select id="product" ${submitting ? "disabled" : ""}><option>DSH</option></select></label>
    <label>虚拟机基线<select id="baseline" data-focus="baseline" ${submitting ? "disabled" : ""}>${checkpoints}</select></label>
    ${button(submitting ? "正在保存…" : state.options.mode === "dsh" ? `真实运行 ${selected.length} 场 · DSH` : `添加 ${selected.length} 场 · DSH`, "submit", "", "primary",
      !selected.length || submitting || pending.length || !connected ? "disabled" : "")}</div></section>`;
}

function recordsPage() {
  const sources = ["全部", "历史回放", ...ACTIVITIES];
  const filters = sources.map((name) => {
    const count = name === "历史回放" ? state.records.filter((row) => row.source === name).length : state.counts[name];
    return `<button class="filter" type="button" data-action="filter" data-value="${name}" data-focus="filter:${name}"
       aria-pressed="${filter === name}">${name}${count === undefined ? "" : ` ${count}`}</button>`;
  }).join("");
  const runs = state.records.filter((run) => filter === "全部" || (filter === "历史回放" ?
    run.source === filter : run.source !== "历史回放" && run.activity === filter))
    .sort((a, b) => (Date.parse(b.started_at) || 0) - (Date.parse(a.started_at) || 0));
  const body = runs.map((run) => `<tr><td>
    <button type="button" class="text-button" data-action="record" data-value="${run.id}" data-focus="record:${run.id}">
    ${escape(run.title)}</button><span class="sub">${escape(run.condition || `版本 ${run.version}`)}</span></td>
    <td>${sourceChip(run.source)}<span class="sub">${escape(run.activity)}</span></td>
    <td>DSH <span class="sub">${escape(run.baseline)}</span></td>
    <td><time datetime="${escape(run.started_at)}">${stamp(run.started_at, dateFormat)}</time></td>
    <td>${chip(FLOW[run.state], run.state === "closed" ? "success" : run.state === "blocked" ? "error" : "active")}</td>
    <td>${escape(run.score)}<span class="sub">${filesLabel(run)}</span></td></tr>`).join("");
  return `${pageHeader("运行记录", "查看交付、过程及裁定依据。")}
    <div class="filters" aria-label="按记录来源筛选">${filters}</div>
    <p class="small records-description">历史回放保留原有证据。活动数字统计本工作台新增记录，产品运行按已确认提交计数。</p>
    <div class="card table-wrap">${runs.length ? `<table class="record-table"><thead><tr>
      <th scope="col">题目 / 条件</th><th scope="col">来源 / 活动</th><th scope="col">产品</th>
      <th scope="col">开场 · 北京时间</th><th scope="col">流程</th><th scope="col">结果</th></tr></thead><tbody>${body}</tbody></table>` :
      '<div class="empty"><strong>当前筛选下没有运行记录</strong></div>'}</div>`;
}

function pendingBatches() {
  const prefix = `workbench-${state.options.mode}-batch-`;
  return Object.keys(localStorage).filter((key) => key.startsWith(prefix)).map((key) =>
    ({key, body: JSON.parse(localStorage.getItem(key))}));
}

async function checkPending(retry = false) {
  for (const entry of pendingBatches()) {
    try {
      const reply = await request(`/api/batches/${entry.body.id}`);
      if (reply.jobs.length) {
        localStorage.removeItem(entry.key);
        toast(`已确认原批次的 ${reply.jobs.length} 场任务。`);
      } else if (retry) {
        await sendBatch(entry.key, entry.body);
      }
    } catch {
      if (retry) toast("暂时无法确认回执，已保存原入队请求。");
    }
  }
}

function render(force = false) {
  if (!state) return;
  const markup = page === "queue" ? queuePage() : page === "library" ? libraryPage() : recordsPage();
  if (!force && markup === renderKey) return;
  const active = view.contains(document.activeElement) ? document.activeElement.dataset.focus : null;
  view.innerHTML = markup;
  renderKey = markup;
  restoreFocus(active);
  updateWaitingClock();
}

function restoreFocus(key) {
  if (!key) return;
  const target = view.querySelector(`[data-focus="${CSS.escape(key)}"]`);
  if (target && !target.disabled) target.focus({preventScroll: true});
}

function updateWaitingClock() {
  const label = $("#waiting-clock");
  const run = state?.jobs.find((row) => row.state === "running");
  if (!label || !run?.last_event_at) return;
  const minutes = Math.max(0, Math.floor((Date.now() - Date.parse(run.last_event_at)) / 60000));
  label.textContent = minutes >= 2 ? ` · 距上次记录 ${minutes} 分钟` : "";
}

function showConnection() {
  const connection = $("#connection");
  connection.classList.toggle("offline", !connected);
  connection.innerHTML = `<i></i>${connected ? "宿主机已连接" : "连接中断"}`;
  $("#machine-mode").textContent = state?.options.mode === "dsh" ? "本机 DSH · 正式模式" : "本机工作区 · 模拟自测";
  $("#last-sync").textContent = connected ? `北京时间 · ${stamp(state.at)} 已同步` :
    `连接中断 · 上次同步 ${stamp(state?.at)}（北京时间）`;
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    state = await request("/api/state");
    connected = true;
    baseline ||= state.options.baseline;
    if (!pendingReady) {
      pendingReady = true;
      await checkPending();
    }
    if (page === "library" && !catalog && !catalogError) await fetchCatalog();
    if (log) void pullLog();
  } catch (error) {
    connected = false;
    if (!state) view.innerHTML = '<h1>运行队列</h1><p class="banner">无法连接宿主机。页面会继续重新连接。</p>';
  } finally {
    showConnection();
    render();
    refreshing = false;
  }
}

async function fetchCatalog() {
  try {
    catalog = await request("/api/catalog");
    catalogError = null;
    selected = selected.filter((key) => catalog.some((row) => row.key === key && !row.error));
  } catch (error) {
    catalogError = error.message;
  }
  render();
}

function navigate() {
  const id = location.hash.slice(1);
  page = Object.hasOwn(LABELS, id) ? id : "queue";
  for (const link of document.querySelectorAll("nav [data-page]")) {
    if (link.dataset.page === page) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (page === "library" && state && !catalog) void fetchCatalog();
  render();
}

async function sendBatch(storageKey, body) {
  try {
    const response = await request("/api/batches", {method: "POST",
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    localStorage.removeItem(storageKey);
    selected = [];
    toast(`已按顺序添加 ${response.jobs.length} 场。`);
    location.hash = "#queue";
    await refresh();
  } catch (error) {
    if (error.confirmedFailure) {
      localStorage.removeItem(storageKey);
      toast(error.message);
    } else {
      toast("入队回执尚未收到。重试将沿用原凭据。");
    }
  }
}

async function submit(retry = false) {
  if (submitting || (!retry && !selected.length)) return;
  submitting = true;
  try {
    if (retry) {
      render();
      await checkPending(true);
    } else {
      const body = {id: crypto.randomUUID(), product: $("#product").value, baseline,
        items: selected.map((key) => ({key, fingerprint: catalog.find((item) => item.key === key).fingerprint}))};
      const storageKey = `workbench-${state.options.mode}-batch-${body.id}`;
      localStorage.setItem(storageKey, JSON.stringify(body));
      render();
      await sendBatch(storageKey, body);
    }
  } finally {
    submitting = false;
    render();
  }
}

function currentRun() {
  return state.jobs.find((row) => ["running", "blocked"].includes(row.state)) ||
    state.jobs.filter((row) => row.state === "closed").at(-1) || state.replay;
}

function openLog(id, stage = "all", focus = "", reveal = "") {
  const run = state.jobs.find((row) => row.id === id) || state.records.find((row) => row.id === id);
  if (!run) return;
  logTarget = focus;
  log = {id, after: 0, events: [], inFlight: false, reveal};
  $("#log-heading").textContent = run.title;
  $("#log-meta").textContent = [run.condition, run.product, run.source, run.run_id, "北京时间"].filter(Boolean).join(" · ");
  $("#log-stage").value = stage;
  $("#log-content").textContent = "正在读取场次事件…";
  logDialog.showModal();
  void pullLog();
}

function paintLog() {
  const target = $("#log-content");
  const opened = new Set([...target.querySelectorAll("details[open]")].map((node) => node.dataset.evidence));
  const active = target.contains(document.activeElement) ? document.activeElement.closest("details")?.dataset.evidence : null;
  const stage = $("#log-stage").value;
  const events = log.events.filter((ev) => stage === "all" || ev.stage === Number(stage))
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || a.seq - b.seq);
  target.innerHTML = events.length ? `<ol class="timeline">${events.map((row) => timeEvent(row, true)).join("")}</ol>` :
    '<p class="empty">这个阶段还没有记录。</p>';
  for (const node of target.querySelectorAll("details")) {
    node.open = opened.has(node.dataset.evidence) || node.dataset.evidence === log.reveal;
    if (node.dataset.evidence === log.reveal) {
      node.querySelector("summary").focus({preventScroll: true});
      node.scrollIntoView({block: "nearest"});
      log.reveal = "";
    }
    if (node.dataset.evidence === active) node.querySelector("summary").focus({preventScroll: true});
  }
}

async function pullLog() {
  const current = log;
  if (!current || current.inFlight) return;
  current.inFlight = true;
  try {
    const events = await request(`/api/runs/${encodeURIComponent(current.id)}/events?after=${current.after}`);
    if (log !== current) return;
    current.events.push(...events);
    current.after = events.at(-1)?.seq || current.after;
    if (events.length || !current.events.length) paintLog();
  } catch {
    if (log === current && !current.events.length) $("#log-content").textContent = "暂时无法读到日志，正在尝试重连。";
  } finally {
    current.inFlight = false;
  }
}

function showDetail(run) {
  const prefix = `/api/runs/${encodeURIComponent(run.id)}/source?key=`;
  const files = run.files.map((file) => `<li>${file.download ?
    `<a href="${prefix}${file.download}">${escape(file.path)}</a>` : escape(file.path)}
    <span class="muted small"> · ${file.exists ? "已回收" : file.status === "missing" ? "已确认缺失" : "待核验"}</span></li>`).join("");
  const sources = run.sources.map((source) => `<li>${source.available ?
    `<a href="${prefix}${source.key}">${escape(source.label)}</a>` : `${escape(source.label)}（原件暂不可读）`}</li>`).join("");
  const fields = Object.entries(run.record).map(([key, val]) =>
    `<dt>${escape(key)}</dt><dd>${typeof val === "object" && val !== null ?
      `<details><summary>展开</summary><pre>${escape(pretty(val))}</pre></details>` : escape(val ?? "待核验")}</dd>`).join("");
  $("#detail-heading").textContent = run.title;
  $("#detail-meta").textContent = [run.condition, run.product, run.source, run.run_id].filter(Boolean).join(" · ");
  $("#detail-content").innerHTML = `<article><h3>交付与结果</h3>
    <p>${filesLabel(run)} · ${escape(run.score)}</p>${files ? `<ul class="files-list">${files}</ul>` : ""}
    ${run.reason ? `<p class="banner">${escape(run.reason)}</p>` : ""}
    ${run.state !== "queued" && run.activity !== "接口预检" ? button("查看场次日志", "detail-log", run.id) : ""}</article>
    ${run.task ? `<article><details ${run.state === "queued" ? "open" : ""}><summary>冻结题包 · 题面和输入</summary>
      <p class="small muted">题包位置 ${escape(run.package)}</p><pre>${escape(run.task.prompt)}</pre>
      <ul class="files-list">${run.task.inputs.map((name) =>
        `<li>${escape(name)} <span class="path">SHA-256 ${escape(run.task.hashes[name])}</span></li>`).join("")}</ul>
      <p>预期交付 ${escape(run.task.outputs.join("、"))}</p></details></article>` : ""}
    <article><h3>原始依据</h3><ul class="files-list">${sources}</ul></article>
    <article><details><summary>展开运行信息与裁定</summary><dl class="detail-grid">${fields}</dl></details></article>`;
}

async function openDetail(id, focus) {
  detailTarget = focus;
  $("#detail-heading").textContent = "运行记录";
  $("#detail-meta").textContent = "";
  $("#detail-content").textContent = "正在读取…";
  detailDialog.showModal();
  try {
    showDetail(await request(`/api/runs/${encodeURIComponent(id)}`));
  } catch (error) {
    $("#detail-content").textContent = error.message;
  }
}

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const {action, value, focus} = target.dataset;
  if (action === "add") location.hash = "#library";
  if (action === "new-task") $("#task-dialog").showModal();
  if (action === "refresh-library") void fetchCatalog();
  if (action === "preview") { previewKey = value; render(); }
  if (action === "filter") { filter = value; render(); }
  if (action === "log") openLog(value, "all", focus);
  if (action === "stage") openLog(currentRun().id, value, focus);
  if (action === "event-log") {
    const run = currentRun();
    const event = run.latest.find((row) => String(row.seq) === value);
    if (event) openLog(run.id, String(event.stage), focus, value);
  }
  if (action === "record") void openDetail(value, focus);
  if (action === "detail-log") {
    detailDialog.close();
    openLog(value, "all", detailTarget);
  }
  if (action === "up" || action === "down") {
    const from = selected.indexOf(value);
    const to = from + (action === "up" ? -1 : 1);
    if (from >= 0 && to >= 0 && to < selected.length) {
      [selected[from], selected[to]] = [selected[to], selected[from]];
      render();
      restoreFocus(target.disabled || (to === 0 && action === "up") ||
        (to === selected.length-1 && action === "down") ? `${action === "up" ? "down" : "up"}:${value}` : focus);
    }
  }
  if (action === "submit" || action === "retry") void submit(action === "retry");
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-task]")) {
    const key = event.target.dataset.task;
    if (event.target.checked) selected.push(key);
    else selected = selected.filter((value) => value !== key);
    previewKey = key;
    render();
  }
  if (event.target.id === "baseline") { baseline = event.target.value; render(); }
  if (event.target.id === "log-stage" && log) paintLog();
});

logDialog.addEventListener("close", () => { log = null; restoreFocus(logTarget); });
detailDialog.addEventListener("close", () => { if (!logDialog.open) restoreFocus(detailTarget); });
$("#log-stage").innerHTML = '<option value="all">全部阶段</option>' +
  STAGES.map((name, index) => `<option value="${index}">${name}</option>`).join("");
window.addEventListener("hashchange", navigate);
window.addEventListener("online", refresh);
const taskForm = $("#task-form");
function updateTaskFields() {
  const writing = taskForm.elements.methodPicker.value === "write";
  $("#task-fields").hidden = !writing;
  $("#task-fields").disabled = !writing;
}
taskForm.elements.methodPicker.addEventListener("change", updateTaskFields);
taskForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(taskForm);
  const body = {directory: form.get("directory")};
  if (form.get("methodPicker") === "write") {
    const paths = (key) => form.get(key).split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    body.manifest = {task_id: form.get("title").trim(), title: form.get("title").trim(),
      version: form.get("version").trim(), prompt: form.get("prompt"), inputs: paths("inputs"), outputs: paths("outputs")};
  }
  const save = taskForm.querySelector("button[type=submit]");
  if (save.disabled) return;
  save.disabled = true;
  $("#task-error").textContent = "";
  try {
    const result = await request("/api/tasks", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body), signal: null});
    previewKey = result.key;
    await fetchCatalog();
    $("#task-dialog").close();
    taskForm.reset();
    updateTaskFields();
    toast("已保存到题库。勾选题目后即可入队。");
  } catch (error) {
    $("#task-error").textContent = error.message;
  } finally {
    save.disabled = false;
  }
});
$(".skip").addEventListener("click", (event) => {
  event.preventDefault();
  $("#main").focus();
});
navigate();
void refresh();
setInterval(refresh, 1200);
setInterval(updateWaitingClock, 5000);
