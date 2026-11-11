"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

const QUOTES = [
  "Wubba lubba dub dub!", "Get schwifty with your downloads.", "I'm Download Rick!",
  "Nobody exists on purpose. Nobody belongs anywhere. Everybody's gonna download.",
  "To live is to risk it all. Otherwise you're just an inert chunk of randomly assembled .part files.",
  "Sometimes science is more art than science, Morty.", "Show me what you got!",
  "Ooh-wee! That's a lotta bandwidth.", "Existence is pain to a Meeseeks — and to slow hosters.",
  "Don't think about it, Morty. Just paste the magnet.",
];
const KIND = {
  torrent: "🧲 Torrent", torrent_url: "🧲 Torrent URL", debrid: "🔓 Hoster", mega: "Ⓜ MEGA",
  gofile: "📦 Gofile", ytdlp: "📺 Interdimensional Cable", direct: "🔗 Direct",
};
const STATE = {
  awaiting_provider: "Choose a dimension", awaiting_files: "Pick files", queued: "In the garage",
  remote: "Cooking remotely", downloading: "Downloading", done: "Completed", error: "Aw jeez", paused: "Cryo-frozen",
  canceled: "Canceled",
};
const EMPTY = {
  all: "Nothing in the queue. Paste a link and open a portal, Morty.",
  active: "Nothing's moving. Even the Meeseeks went home.",
  done: "Nothing finished yet. Patience is a virtue, Morty. Not one of mine.",
  error: "No problems. Which is itself suspicious.",
};

let me = null, filter = "all", providers = { providers: [], usable: [] };
let pickQueue = [], picking = null, snoozed = new Set();
let filesJob = null, filesSnoozed = new Set(), lastState = new Map();
const cards = new Map();

// ------------------------------------------------------------------ api
async function api(path, opts = {}) {
  const headers = { "X-RickWire": "1", ...(opts.headers || {}) };
  let body = opts.body;
  if (body && !(body instanceof FormData)) { headers["Content-Type"] = "application/json"; body = JSON.stringify(body); }
  const r = await fetch(path, { method: opts.method || "GET", headers, body, credentials: "same-origin" });
  if (r.status === 401) { onUnauthed(); throw new Error("Not paired"); }
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(data?.detail || data || r.statusText);
  return data;
}
function toast(msg, bad = false) {
  const t = document.createElement("div");
  t.className = "toast" + (bad ? " bad" : "");
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}
const fmtBytes = (n) => {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
};
const fmtEta = (s) => {
  if (!isFinite(s) || s <= 0) return "";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};
function onUnauthed() {
  const loop = ["127.0.0.1", "localhost", "[::1]"].includes(location.hostname) && !me?.remote;
  if (loop) { document.body.innerHTML = '<main><div class="empty"><div class="portal"></div>Open RickWire from its desktop window (or the tray icon), Morty.</div></main>'; }
  else location.replace("/pair.html");
}

// ------------------------------------------------------------------ boot
async function boot() {
  $("#quote").textContent = QUOTES[Math.floor(Math.random() * QUOTES.length)];
  setInterval(() => { $("#quote").textContent = QUOTES[Math.floor(Math.random() * QUOTES.length)]; }, 20000);
  try { me = await api("/api/me"); } catch { return; }
  if (!me.authenticated) return onUnauthed();
  if (me.admin) $$(".admin-only").forEach((e) => e.classList.remove("hidden"));
  if (me.login) {
    $("#signOutBtn").classList.remove("hidden");
    $("#signOutBtn").onclick = async () => {
      try { await api("/api/auth/logout", { method: "POST" }); } catch {}
      location.replace("/pair.html");
    };
  }
  if (me.platform && me.platform !== "win32") {   // Linux desktop: different keyring, no "Default apps" page
    $$(".kr").forEach((e) => (e.textContent = "your system keyring (GNOME Keyring / KWallet)"));
    $("#defApps").classList.add("hidden");
  }
  if ("serviceWorker" in navigator && window.isSecureContext && !me.admin) navigator.serviceWorker.register("/sw.js").catch(() => {});
  await loadProviders();
  wire();
  poll();
  const s = await api("/api/settings").catch(() => ({ onboarded: true }));
  applySpin(s.spin_portal);
  if (me.admin) {
    if (!s.onboarded) openSetup().catch((e) => toast(e.message, true));
    else if (location.hash === "#settings") openSettings().catch((e) => toast(e.message, true));
  }
  const shared = sessionStorage.getItem("rw_shared");
  if (shared) { sessionStorage.removeItem("rw_shared"); $("#links").value = shared; }
}

async function loadProviders() {
  providers = await api("/api/providers");
  const sel = $("#provider");
  const usable = providers.providers.filter((p) => providers.usable.includes(p.id));
  sel.innerHTML = "";
  const auto = new Option(usable.length > 1 && providers.ask ? "🌀 Provider: ask me" : "🌀 Provider: default", "");
  sel.add(auto);
  usable.forEach((p) => sel.add(new Option(`🪐 ${p.name}`, p.id)));
  sel.classList.toggle("hidden", usable.length < 2);
}

// ------------------------------------------------------------------ polling / render
let pollTimer = null, failures = 0;
async function poll() {
  clearTimeout(pollTimer);
  try {
    const { jobs } = await api("/api/jobs");
    failures = 0;
    setConn(true);
    render(jobs);
  } catch (e) {
    if (++failures > 1) setConn(false);
  }
  pollTimer = setTimeout(poll, document.hidden ? 4000 : 1000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
function setConn(ok) {
  const c = $("#conn");
  c.className = "pill " + (ok ? "ok" : "bad");
  c.textContent = ok ? (me?.admin ? "portal open" : `${me?.login ? "signed in" : "paired"} · ${me?.name || "phone"}`) : "portal closed";
}

function matches(j) {
  if (filter === "active") return ["queued", "remote", "downloading", "awaiting_provider", "awaiting_files", "paused"].includes(j.state);
  if (filter === "done") return j.state === "done";
  if (filter === "error") return j.state === "error";
  return true;
}

function render(jobs) {
  const list = $("#jobs");
  const visible = jobs.filter(matches);
  const ids = new Set(visible.map((j) => j.id));
  for (const [id, el] of cards) if (!ids.has(id)) { el.remove(); cards.delete(id); }
  visible.forEach((j, idx) => {
    let el = cards.get(j.id);
    if (!el) { el = makeCard(j.id); cards.set(j.id, el); }
    if (list.children[idx] !== el) list.insertBefore(el, list.children[idx] || null);
    updateCard(el, j);
  });
  $("#empty").classList.toggle("hidden", visible.length > 0);
  $("#emptyText").textContent = EMPTY[filter];

  const active = jobs.filter((j) => j.state === "downloading");
  const speed = active.reduce((a, j) => a + (j.speed || 0), 0);
  $("#stats").textContent = active.length ? `${active.length} active · ${fmtBytes(speed)}/s` : `${jobs.length} total`;
  document.body.classList.toggle("busy", active.length > 0);
  const live = jobs.filter((j) => ["queued", "remote", "downloading", "awaiting_provider", "awaiting_files"].includes(j.state)).length;
  $("#qCount").textContent = live || "";
  $("#retryBtn").classList.toggle("hidden", !jobs.some((j) => j.state === "error"));

  // in-app "finished" toast (the desktop notification says the same)
  for (const j of jobs) {
    const prev = lastState.get(j.id);
    if (prev && prev !== "done" && j.state === "done") toast(`Wubba lubba dub dub! ${j.name} is done`);
    lastState.set(j.id, j.state);
  }

  // answered somewhere else (phone or desktop) or removed: close the open question here too
  const stateOf = (id) => jobs.find((j) => j.id === id)?.state;
  if (filesJob && stateOf(filesJob) !== "awaiting_files") { filesJob = null; if ($("#filesDlg").open) $("#filesDlg").close(); }
  if (picking && stateOf(picking) !== "awaiting_provider") { picking = null; if ($("#pickDlg").open) $("#pickDlg").close(); }

  // file choices
  if (!filesJob && !picking) {
    const wf = jobs.find((j) => j.state === "awaiting_files" && !filesSnoozed.has(j.id));
    if (wf) openFiles(wf);
  }

  // provider questions
  const waiting = jobs.filter((j) => j.state === "awaiting_provider" && !snoozed.has(j.id));
  pickQueue = waiting.map((j) => j);
  if (!picking && pickQueue.length) openPicker(pickQueue[0]);
}

function makeCard(id) {
  const el = document.createElement("article");
  el.className = "card job";
  el.innerHTML = `
    <div class="job-head">
      <div class="job-title"><b></b><div class="meta"><span class="tag kind"></span><span class="state"></span><span class="nums"></span><span class="via"></span></div></div>
      <div class="actions">
        <button class="btn icon" data-a="pick" title="Choose provider">🪐</button>
        <button class="btn icon" data-a="choose" title="Pick files">☑</button>
        <button class="btn icon" data-a="pause" title="Pause">⏸</button>
        <button class="btn icon" data-a="resume" title="Resume">▶</button>
        <button class="btn icon" data-a="retry" title="Retry">↻</button>
        <button class="btn icon" data-a="sort" title="Rename &amp; sort into Movies / TV folders">🏷</button>
        <button class="btn icon" data-a="open" title="Show in folder">📂</button>
        <button class="btn icon" data-a="files" title="Files">☰</button>
        <button class="btn icon danger" data-a="remove" title="Remove">✕</button>
      </div>
    </div>
    <div class="progress"><i></i></div>
    <div class="msg"></div>
    <div class="files hidden"></div>`;
  el.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-a]");
    if (b) act(id, b.dataset.a, el);
  });
  return el;
}

function updateCard(el, j) {
  el._job = j;
  el.className = `card job ${j.state}`;
  $("b", el).textContent = j.name || j.source;
  $("b", el).title = j.source;
  $(".kind", el).textContent = KIND[j.kind] || j.kind;
  const st = $(".state", el);
  st.className = `state ${j.state}`;
  st.textContent = j.state === "remote" && j.provider ? `Cooking at ${provName(j.provider)}` : STATE[j.state] || j.state;
  let pct = 0, nums = "";
  if (j.state === "remote") { pct = j.remote_progress || 0; nums = `${pct.toFixed(1)}%${j.size ? " of " + fmtBytes(j.size) : ""}`; }
  else if (j.size) { pct = Math.min(100, (j.done / j.size) * 100); nums = `${fmtBytes(j.done)} / ${fmtBytes(j.size)} · ${pct.toFixed(1)}%`; }
  else if (j.done) nums = fmtBytes(j.done);
  if (j.state === "done") pct = 100;
  if (j.state === "downloading" && j.speed) nums += ` · ${fmtBytes(j.speed)}/s` + (j.size ? ` · ${fmtEta((j.size - j.done) / j.speed)}` : "");
  $(".nums", el).textContent = nums;
  $(".via", el).textContent = j.provider ? `via ${provName(j.provider)}` : "";
  $(".progress > i", el).style.width = `${pct}%`;
  $(".msg", el).textContent = j.message || "";
  const show = (a, on) => $(`[data-a=${a}]`, el).classList.toggle("hidden", !on);
  show("pick", j.state === "awaiting_provider");
  show("choose", j.state === "awaiting_files");
  show("pause", ["queued", "remote", "downloading"].includes(j.state));
  show("resume", j.state === "paused");
  show("retry", j.state === "error");
  show("sort", !!me?.admin && j.state === "done" && !j.sorted && j.kind !== "ytdlp");
  show("open", !!me?.admin && ["done", "downloading"].includes(j.state));
  show("files", (j.files || []).length > 1);
  const fl = $(".files", el);
  if (!fl.classList.contains("hidden")) renderFiles(fl, j);
}
function renderFiles(fl, j) {
  fl.replaceChildren(...(j.files || []).map((f) => {
    const d = document.createElement("div");
    const a = document.createElement("span"); a.textContent = f.name;
    const b = document.createElement("span"); b.textContent = f.size ? `${Math.round((f.done / f.size) * 100)}% · ${fmtBytes(f.size)}` : fmtBytes(f.done);
    d.append(a, b); return d;
  }));
}
const provName = (id) => providers.providers.find((p) => p.id === id)?.name || id;

async function act(id, a, el) {
  try {
    if (a === "pause") await api(`/api/jobs/${id}/pause`, { method: "POST" });
    else if (a === "resume" || a === "retry") await api(`/api/jobs/${id}/resume`, { method: "POST" });
    else if (a === "choose") { filesSnoozed.delete(id); openFiles(el._job); }
    else if (a === "sort") return openSort(el._job);
    else if (a === "open") await api(`/api/jobs/${id}/open`, { method: "POST" });
    else if (a === "pick") { snoozed.delete(id); openPicker(el._job); }
    else if (a === "files") { const fl = $(".files", el); fl.classList.toggle("hidden"); renderFiles(fl, el._job); }
    else if (a === "remove") return confirmRemove(el._job);
    poll();
  } catch (e) { toast(e.message, true); }
}

function confirmRemove(j) {
  const d = $("#rmDlg");
  $("#rmName").textContent = j.name;
  $("#rmFiles").checked = false;
  $("#rmGo").onclick = async () => {
    d.close();
    try { await api(`/api/jobs/${j.id}?delete_files=${$("#rmFiles").checked}`, { method: "DELETE" }); poll(); }
    catch (e) { toast(e.message, true); }
  };
  d.showModal();
}

// ------------------------------------------------------------------ provider picker
function openPicker(j) {
  picking = j.id;
  $("#pickName").textContent = j.name;
  $("#pickRemember").checked = false;
  const box = $("#pickChoices");
  box.replaceChildren(...providers.providers.filter((p) => providers.usable.includes(p.id)).map((p) => {
    const b = document.createElement("button");
    b.className = "choice";
    b.innerHTML = `<div class="portal"></div><div><b></b><small>${p.id === providers.default ? "current default" : "dimension " + p.id.toUpperCase().slice(0, 4) + "-137"}</small></div>`;
    $("b", b).textContent = p.name;
    b.onclick = async () => {
      const remember = $("#pickRemember").checked;
      try {
        await api(`/api/jobs/${j.id}/provider`, { method: "POST", body: { provider: p.id, remember } });
        // apply to everything else still waiting in this batch, too
        for (const other of pickQueue) if (other.id !== j.id && (remember || pickQueue.length > 1))
          await api(`/api/jobs/${other.id}/provider`, { method: "POST", body: { provider: p.id, remember: false } }).catch(() => {});
        if (remember) await loadProviders();
        toast(`Portal opened to ${p.name}`);
      } catch (e) { toast(e.message, true); }
      closePicker();
    };
    return b;
  }));
  if (!$("#pickDlg").open) $("#pickDlg").showModal();
}
function closePicker() { picking = null; $("#pickDlg").close(); poll(); }
$("#pickLater").onclick = () => { pickQueue.forEach((j) => snoozed.add(j.id)); closePicker(); };
$("#pickDlg").addEventListener("cancel", () => { pickQueue.forEach((j) => snoozed.add(j.id)); picking = null; });

// ------------------------------------------------------------------ file picker
const VIDEO_RE = /\.(mkv|mp4|avi|m4v|mov|wmv|ts|m2ts|webm|mpg|mpeg|flv)$/i;
const JUNK_RE = /(^|[\/ ._\-\[(])sample([ ._\-\])]|$)|\.(nfo|txt|url|lnk|html?|website)$/i;
function openFiles(j) {
  filesJob = j.id;
  $("#filesName").textContent = j.name;
  $("#filesFilter").value = "";
  const box = $("#filesList");
  box.replaceChildren(...(j.choices || []).map((c) => {
    const l = el("label");
    const cb = el("input"); cb.type = "checkbox"; cb.value = c.name; cb.checked = !JUNK_RE.test(c.name);
    cb.onchange = filesTotals;
    l.append(cb, el("span", "fn", c.name), el("span", "fs", fmtBytes(c.size)));
    l._size = c.size;
    return l;
  }));
  filesTotals();
  if (!$("#filesDlg").open) $("#filesDlg").showModal();
}
function filesTotals() {
  const rows = $$("#filesList label");
  let n = 0, size = 0;
  rows.forEach((l) => { const on = $("input", l).checked; l.classList.toggle("off", !on); if (on) { n++; size += l._size || 0; } });
  $("#filesTotal").textContent = `${n} of ${rows.length} files · ${fmtBytes(size)}`;
  $("#filesGo").disabled = !n;
}
function setFiles(fn) {
  $$("#filesList label").forEach((l) => { if (!l.classList.contains("hidden")) $("input", l).checked = fn($("input", l).value); });
  filesTotals();
}
function closeFiles(snooze) {
  if (snooze && filesJob) filesSnoozed.add(filesJob);
  filesJob = null;
  if ($("#filesDlg").open) $("#filesDlg").close();
  poll();
}
function wireFiles() {
  $("#filesAll").onclick = () => setFiles(() => true);
  $("#filesNone").onclick = () => setFiles(() => false);
  $("#filesVideo").onclick = () => setFiles((n) => VIDEO_RE.test(n) && !JUNK_RE.test(n));
  $("#filesFilter").oninput = () => {
    const q = $("#filesFilter").value.trim().toLowerCase();
    $$("#filesList label").forEach((l) => l.classList.toggle("hidden", !!q && !$("input", l).value.toLowerCase().includes(q)));
  };
  $("#filesLater").onclick = () => closeFiles(true);
  $("#filesDlg").addEventListener("cancel", (e) => { e.preventDefault(); closeFiles(true); });
  $("#filesGo").onclick = async () => {
    const names = $$("#filesList input:checked").map((c) => c.value);
    try { await api(`/api/jobs/${filesJob}/files`, { method: "POST", body: { names } }); toast(`${names.length} file${names.length > 1 ? "s" : ""} on the way`); closeFiles(false); }
    catch (e) { toast(e.message, true); }
  };
}

// ------------------------------------------------------------------ rename & sort
async function openSort(j) {
  try {
    const { moves } = await api(`/api/jobs/${j.id}/sort`);
    $("#sortName").textContent = j.name;
    const plan = $("#sortPlan");
    if (!moves.length) {
      plan.replaceChildren(el("div", null, "Couldn't tell whether this is a movie or a TV episode, so there's nothing to rename."));
      $("#sortGo").classList.add("hidden");
    } else {
      plan.replaceChildren(...moves.map((m) => { const d = el("div"); d.append(el("div", "hint", m.from), el("div", "to", `→ ${m.to}`)); return d; }));
      $("#sortGo").classList.remove("hidden");
    }
    $("#sortGo").onclick = async () => {
      $("#sortDlg").close();
      try { const r = await api(`/api/jobs/${j.id}/sort`, { method: "POST" }); toast(r.message); poll(); }
      catch (e) { toast(e.message, true); }
    };
    $("#sortDlg").showModal();
  } catch (e) { toast(e.message, true); }
}
const sortPref = () => !!$(".sortPref")?.checked;
function wireSortPref() {
  let on = false;
  try { on = localStorage.getItem("rw_sort") === "1"; } catch {}
  $$(".sortPref").forEach((c) => {
    c.checked = on;
    c.onchange = () => {
      $$(".sortPref").forEach((o) => (o.checked = c.checked));
      try { localStorage.setItem("rw_sort", c.checked ? "1" : "0"); } catch {}
    };
  });
}

// ------------------------------------------------------------------ cache check
const SHORT = { realdebrid: "RD", alldebrid: "AD", torbox: "TB", premiumize: "PM" };
const cacheHits = {};   // pid -> { hash: true|false|null }
function btih(m) {
  const x = /btih:([0-9a-f]{40})/i.exec(m || "");
  return x ? x[1].toLowerCase() : null;
}
function cacheProviders(lookupOnly) {
  const sel = $("#provider").value;
  let ids = sel ? [sel] : providers.usable.slice();
  if (lookupOnly) ids = ids.filter((id) => providers.providers.find((p) => p.id === id)?.cache_check === "lookup");
  return ids;
}
function cacheTags(hash) {
  const box = el("span", "cache-tags");
  box.dataset.hash = hash || "";
  paintTags(box);
  return box;
}
function paintTags(box) {
  const h = box.dataset.hash;
  if (!h) return;
  const tags = [];
  for (const pid of Object.keys(cacheHits)) {
    if (!(h in cacheHits[pid])) continue;
    const v = cacheHits[pid][h];
    const multi = Object.keys(cacheHits).length > 1;
    const name = multi ? SHORT[pid] || pid : "";
    const sfx = name ? " " + name : "";
    const [cls, text, tip] = v === true ? [" hit", `⚡ cached${sfx}`, "already has it - downloads instantly"]
      : v === false ? ["", `not cached${sfx}`, "would have to download it first"]
      : v === "blocked" ? [" blocked", `🚫 blocked${sfx}`, "refuses this torrent (its content filter)"]
      : ["", `?${sfx}`, "couldn't be asked"];
    const t = el("span", "ctag" + cls, text);
    t.title = `${provName(pid)} ${tip}`;
    tags.push(t);
  }
  box.replaceChildren(...tags);
}
function repaintCache() {
  $$(".cache-tags").forEach(paintTags);
  $$(".qs .btn[data-hash]").forEach((b) => {
    const hit = Object.values(cacheHits).some((r) => r[b.dataset.hash] === true);
    b.classList.toggle("cached", hit);
    const z = $(".zap", b); if (hit && !z) b.prepend(el("span", "zap", "⚡")); if (!hit && z) z.remove();
  });
}
function visibleMagnets(scope) {
  let nodes;
  if (scope === "searchResults") nodes = $$("#searchResults [data-magnet]");
  else nodes = newsTab === "yts" ? $$("#ytsGrid [data-magnet]") : $$("#eztvList [data-magnet]");
  const seen = new Set(), out = [];
  for (const n of nodes) { const h = btih(n.dataset.magnet); if (h && !seen.has(h)) { seen.add(h); out.push(n.dataset.magnet); } }
  return out;
}
async function runCacheCheck(scope, lookupOnly, btn) {
  const pids = cacheProviders(lookupOnly);
  if (!pids.length) { if (!lookupOnly) toast("Add a debrid provider in Settings first", true); return; }
  let magnets = visibleMagnets(scope);
  if (lookupOnly) magnets = magnets.filter((m) => pids.some((p) => !(btih(m) in (cacheHits[p] || {}))));
  if (!magnets.length) return;
  const limit = lookupOnly ? magnets.length : 60;
  magnets = magnets.slice(0, limit);
  const batch = pids.every((id) => providers.providers.find((p) => p.id === id)?.cache_check === "lookup") ? 25 : 3;
  const label = btn?.textContent;
  if (btn) btn.disabled = true;
  try {
    for (let i = 0; i < magnets.length; i += batch) {
      if (btn) btn.textContent = `⚡ Checking ${Math.min(i + batch, magnets.length)} / ${magnets.length}…`;
      const part = magnets.slice(i, i + batch);
      const { results } = await api("/api/cache", { method: "POST", body: { magnets: part, provider: pids.length === 1 ? pids[0] : null } });
      for (const [pid, r] of Object.entries(results)) if (pids.includes(pid)) Object.assign(cacheHits[pid] ||= {}, r);
      repaintCache();
    }
    if (btn) {
      const n = magnets.filter((m) => Object.values(cacheHits).some((r) => r[btih(m)] === true)).length;
      toast(`${n} of ${magnets.length} already cached`);
    }
  } catch (e) { if (btn) toast(e.message, true); }
  finally { if (btn) { btn.disabled = false; btn.textContent = label; } }
}
function autoCache(scope) { setTimeout(() => runCacheCheck(scope, true, null), 50); }

// ------------------------------------------------------------------ adding
async function addLinks() {
  const text = $("#links").value.trim();
  if (!text) return toast("Paste something first, Morty.", true);
  $("#addBtn").disabled = true;
  try {
    const jobs = await api("/api/jobs", { method: "POST", body: {
      text, mode: $("#mode").value, provider: $("#provider").value || null, audio_only: $("#audioOnly").checked,
      sort: sortPref() } });
    $("#links").value = "";
    toast(`${jobs.length} item${jobs.length > 1 ? "s" : ""} sent through the portal`);
    poll();
  } catch (e) { toast(e.message, true); }
  finally { $("#addBtn").disabled = false; }
}
async function uploadTorrents(files) {
  files = [...files].filter((f) => f.name.toLowerCase().endsWith(".torrent"));
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f));
  fd.append("provider", $("#provider").value || "");
  fd.append("sort", sortPref() ? "true" : "false");
  try { await api("/api/jobs/torrent", { method: "POST", body: fd }); toast(`${files.length} torrent(s) added`); poll(); }
  catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ settings
async function openSettings() {
  const [s, sys, devs, au] = await Promise.all([api("/api/settings"), api("/api/system"), api("/api/devices"), api("/api/auth")]);
  await loadProviders();
  const gf = providers.extra?.gofile || {};
  const rows = providers.providers.map(provRow);
  rows.push(provRow({
    id: "gofile", name: "Gofile (optional, premium)", configured: gf.configured, masked: gf.masked, enabled: true,
    key_url: "https://gofile.io/myprofile", signup_url: "https://gofile.io/premium", extra: true,
    steps: ["Only needed for Gofile links when none of your debrid providers handle Gofile.",
            "Gofile only allows API downloads for premium accounts.",
            "Log in to gofile.io, open My profile and copy the Account token."],
  }));
  $("#provList").replaceChildren(...rows);
  // show live account status for every configured provider
  rows.forEach((r) => r._autotest && r._autotest());
  const def = $("#s_default");
  def.replaceChildren(new Option("(first configured)", ""), ...providers.providers.filter((p) => p.configured).map((p) => new Option(p.name, p.id)));
  def.value = s.default_provider || "";
  $("#s_ask").value = s.ask_provider ? "1" : "0";
  $("#s_hoster").value = s.hoster_mode;
  $("#s_cleanup").checked = s.cleanup_remote;
  $("#s_pick").checked = s.pick_files;
  $("#s_retry").value = s.auto_retry;
  $("#s_dir").value = s.download_dir;
  $("#s_conc").value = s.max_concurrent;
  $("#s_fmt").value = s.ytdlp_format;
  $("#s_afmt").value = s.ytdlp_audio_format;
  $("#s_subs").checked = s.ytdlp_subs;
  $("#s_sublangs").value = s.ytdlp_sub_langs;
  $("#s_autosubs").checked = s.ytdlp_auto_subs;
  $("#s_ffmpeg").value = s.ffmpeg_path;
  $("#s_lan").checked = s.lan_enabled;
  $("#s_https").checked = s.lan_https;
  $("#s_lanport").value = s.lan_port;
  $("#s_cidrs").value = (s.allowed_cidrs || []).join(", ");
  $("#s_tray").checked = s.close_to_tray;
  $("#s_min").checked = s.start_minimized;
  $("#s_spin").checked = s.spin_portal !== false;
  $("#s_search").value = s.search_url;
  $("#s_yts").value = s.yts_api;
  $("#s_eztv").value = s.eztv_api;
  const status = (ok, text) => el("span", ok ? "ok-text" : "bad-text", text);
  $("#toolInfo").replaceChildren(`Settings & queue: ${sys.data_dir}${sys.portable ? " (portable)" : ""}`);
  $("#s_autoyt").checked = s.auto_update_ytdlp;
  $("#s_toolchk").checked = s.check_tool_updates;
  refreshTools();
  renderAssoc(sys.associations);
  $("#lanInfo").textContent = !sys.lan_url ? "LAN access is off."
    : sys.public_url ? `Phone URL: ${sys.public_url} (remote access is on, so phones use it at home too; LAN ${sys.lan_url} forwards there)`
    : `Phone URL: ${sys.lan_url}  (this PC: ${sys.ips.join(", ") || "no LAN IP"})`;
  renderDevices(devs);
  $("#s_remote").checked = s.remote_enabled;
  $("#s_proxies").value = (s.trusted_proxies || []).join(", ");
  $("#s_hosts").value = (s.public_hosts || []).join(", ");
  renderLogin(au);
  const lanIp = (sys.ips || [])[0] || "this PC's IP";
  $("#npmInfo").textContent = `In Nginx Proxy Manager: Proxy Host for your public address → scheme ${s.lan_https ? "https" : "http"}, ` +
    `forward to ${lanIp} port ${s.lan_port}. SSL tab: request a Let's Encrypt certificate, turn on Force SSL and HSTS.` +
    (sys.public_url ? ` Phones (pair + sign-in) now use ${sys.public_url} everywhere.` : "");
  $("#setMsg").textContent = "";
  $("#setDlg").showModal();
}
function provRow(p) {
  const d = document.createElement("div");
  d.className = "prov";
  d.innerHTML = `
    <div class="prov-head"><b></b><span class="hint status"></span>
      <label class="check"><input type="checkbox" class="en"> enabled</label></div>
    <div class="links">
      <a class="btn go key-link" target="_blank" rel="noopener noreferrer">🔑 Get API key ↗</a>
      <a class="btn signup-link" target="_blank" rel="noopener noreferrer">Sign up ↗</a>
      <a class="btn dash-link" target="_blank" rel="noopener noreferrer">Dashboard ↗</a>
      <button class="btn how" type="button">How do I get it?</button>
    </div>
    <ol class="steps hidden"></ol>
    <div class="inline"><input type="password" class="key" autocomplete="off" spellcheck="false">
      <button class="btn icon paste" type="button" title="Paste from clipboard">📋</button>
      <button class="btn icon peek" type="button" title="Show what you typed">👁</button>
      <button class="btn save">Save key</button><button class="btn test">Test</button><button class="btn danger del">Forget</button></div>`;
  $("b", d).textContent = p.name;
  $(".status", d).textContent = p.configured ? `key ${p.masked}` : "not set";
  $(".status", d).className = "hint status " + (p.configured ? "ok-text" : "");
  $(".key", d).placeholder = p.configured ? "Paste a new key to replace the saved one" : "Paste API key";
  $(".en", d).checked = p.enabled;
  $(".en", d).dataset.pid = p.id;
  const link = (sel, url) => { const a = $(sel, d); if (url) a.href = url; else a.remove(); };
  link(".key-link", p.key_url);
  link(".signup-link", p.signup_url);
  link(".dash-link", p.dashboard_url);
  const steps = $(".steps", d);
  steps.replaceChildren(...(p.steps || []).map((s) => Object.assign(document.createElement("li"), { textContent: s })));
  if (!p.configured) steps.classList.remove("hidden");   // new users see the instructions straight away
  $(".how", d).onclick = () => steps.classList.toggle("hidden");
  if (p.extra) { $(".en", d).closest("label").remove(); $(".test", d).remove(); }
  $(".paste", d).onclick = async () => {
    try { $(".key", d).value = (await navigator.clipboard.readText()).trim(); }
    catch { toast("Clipboard blocked — press Ctrl+V in the box instead", true); }
  };
  $(".peek", d).onclick = () => { const k = $(".key", d); k.type = k.type === "password" ? "text" : "password"; };
  const out = (msg, ok) => { const s = $(".status", d); s.textContent = msg; s.className = "hint status " + (ok ? "ok-text" : "bad-text"); };
  const expiry = (e) => {
    if (!e) return "";
    const dt = typeof e === "number" ? new Date(e * 1000) : new Date(e);
    if (isNaN(dt)) return "";
    const days = Math.round((dt - Date.now()) / 864e5);
    return ` · until ${dt.toLocaleDateString()}${days >= 0 && days < 14 ? ` (${days}d left!)` : ""}`;
  };
  const describe = (r) => r.ok
    ? `✓ ${r.account.user || "connected"}${r.account.premium ? " · premium" : " · NOT premium"}${expiry(r.account.expires)}`
    : `✗ ${r.error}`;
  if (p.configured && !p.extra) d._autotest = async () => {
    out(`key ${p.masked} · checking…`, true);
    try { const r = await api(`/api/providers/${p.id}/test`, { method: "POST" }); out(`${p.masked} ${describe(r)}`, r.ok); }
    catch (e) { out(e.message, false); }
  };
  $(".save", d).onclick = async () => {
    const key = $(".key", d).value.trim();
    if (!key) return;
    try { const r = await api(`/api/providers/${p.id}/key`, { method: "PUT", body: { key } }); $(".key", d).value = ""; out(describe(r), r.ok); loadProviders(); }
    catch (e) { out(e.message, false); }
  };
  if (!p.extra) $(".test", d).onclick = async () => { const r = await api(`/api/providers/${p.id}/test`, { method: "POST" }); out(describe(r), r.ok); };
  $(".del", d).onclick = async () => { await api(`/api/providers/${p.id}/key`, { method: "DELETE" }); out("not set", false); loadProviders(); };
  return d;
}
function renderAssoc(a) {
  const parts = [];
  parts.push(a.magnet ? "✓ magnet: links open RickWire" : "✗ magnet: links not registered");
  parts.push(a.torrent ? "✓ .torrent files open RickWire" : "✗ .torrent files not registered");
  const win = !me.platform || me.platform === "win32";
  $("#assocInfo").textContent = parts.join(" · ") + (win
    ? ". If another app still grabs them, pick RickWire in Windows Default apps."
    : ". Registering also adds RickWire to your applications menu.");
}
function renderDevices(devs) {
  const box = $("#devList");
  if (!devs.length) { box.innerHTML = '<div class="hint">No paired phones yet.</div>'; return; }
  box.replaceChildren(...devs.map((dv) => {
    const row = document.createElement("div");
    const label = document.createElement("span");
    label.textContent = `📱 ${dv.name}${dv.remote ? " (home + away)" : ""} — last seen ${new Date(dv.last_seen * 1000).toLocaleString()}`;
    const b = document.createElement("button");
    b.className = "btn danger"; b.textContent = "Revoke";
    b.onclick = async () => { await api(`/api/devices/${dv.id}`, { method: "DELETE" }); renderDevices(await api("/api/devices")); };
    row.append(label, b); return row;
  }));
}
async function saveSettings() {
  const enabled = {};
  $$("#provList .en").forEach((c) => (enabled[c.dataset.pid] = c.checked));
  const body = {
    ask_provider: $("#s_ask").value === "1", default_provider: $("#s_default").value || null,
    hoster_mode: $("#s_hoster").value, cleanup_remote: $("#s_cleanup").checked, providers_enabled: enabled,
    pick_files: $("#s_pick").checked, auto_retry: Math.max(0, parseInt($("#s_retry").value, 10) || 0),
    download_dir: $("#s_dir").value.trim(), max_concurrent: parseInt($("#s_conc").value, 10) || 3,
    ytdlp_format: $("#s_fmt").value.trim() || "bestvideo*+bestaudio/best", ytdlp_audio_format: $("#s_afmt").value,
    ytdlp_subs: $("#s_subs").checked, ytdlp_sub_langs: $("#s_sublangs").value.trim() || "en.*",
    ytdlp_auto_subs: $("#s_autosubs").checked,
    ffmpeg_path: $("#s_ffmpeg").value.trim(), lan_enabled: $("#s_lan").checked, lan_https: $("#s_https").checked,
    lan_port: parseInt($("#s_lanport").value, 10) || 13701,
    allowed_cidrs: $("#s_cidrs").value.split(/[\s,]+/).filter(Boolean),
    close_to_tray: $("#s_tray").checked, start_minimized: $("#s_min").checked, spin_portal: $("#s_spin").checked,
    search_url: $("#s_search").value.trim(), yts_api: $("#s_yts").value.trim(), eztv_api: $("#s_eztv").value.trim(),
    auto_update_ytdlp: $("#s_autoyt").checked, check_tool_updates: $("#s_toolchk").checked,
    remote_enabled: $("#s_remote").checked,
    trusted_proxies: $("#s_proxies").value.split(/[\s,]+/).filter(Boolean),
    public_hosts: $("#s_hosts").value.split(/[\s,]+/).filter(Boolean),
  };
  try {
    const r = await api("/api/settings", { method: "PUT", body });
    applySpin(body.spin_portal);
    await loadProviders();
    $("#setMsg").textContent = r.restart ? "Saved. Restart RickWire for network changes." : "Saved. Wubba lubba dub dub.";
  } catch (e) { $("#setMsg").textContent = e.message; }
}

// portal logo animation on/off (remembered locally too, so the sign-in page matches)
function applySpin(on) {
  const still = on === false;
  document.body.classList.toggle("still", still);
  try { localStorage.setItem("rw_still", still ? "1" : "0"); } catch {}
}

// ------------------------------------------------------------------ web login / 2FA / sessions
function renderLogin(au) {
  $("#loginState").textContent = au.enabled ? `✓ on · user “${au.user}”` : "· off";
  $("#loginState").className = "hint " + (au.enabled ? "ok-text" : "");
  if (au.enabled && !$("#w_user").value) $("#w_user").value = au.user;
  $("#w_pass").value = $("#w_pass2").value = "";
  $("#w_save").textContent = au.enabled ? "Change password" : "Save login";
  $("#w_remove").classList.toggle("hidden", !au.enabled);
  $("#totpBox").classList.toggle("hidden", !au.enabled);
  $("#totpState").textContent = au.totp ? "✓ on" : au.keyring ? "· off (recommended for remote access)" : "· needs a system keyring";
  $("#totpState").className = "hint " + (au.totp ? "ok-text" : "bad-text");
  $("#totpStart").classList.toggle("hidden", au.totp);
  $("#totpOff").classList.toggle("hidden", !au.totp);
  $("#totpSetup").classList.add("hidden");
  renderSessions(au.list || []);
}
function renderSessions(list) {
  const box = $("#sessList");
  if (!list.length) { box.replaceChildren(el("div", "hint", "Nobody is signed in with the web login.")); return; }
  box.replaceChildren(...list.map((x) => {
    const row = el("div");
    const ua = /Android/.test(x.agent) ? "Android" : /iPhone|iPad/.test(x.agent) ? "iPhone" : /Windows/.test(x.agent) ? "Windows" : /Mac/.test(x.agent) ? "Mac" : "Browser";
    row.append(el("span", null, `🌐 ${ua} · ${x.ip} · last seen ${new Date(x.last_seen * 1000).toLocaleString()}`));
    const b = el("button", "btn danger", "Sign out");
    b.onclick = async () => renderSessions((await api(`/api/auth/sessions?sid=${x.id}`, { method: "DELETE" })).list);
    row.append(b);
    return row;
  }));
}
function wireLogin() {
  const msg = (t) => ($("#setMsg").textContent = t);
  $("#w_save").onclick = async () => {
    const username = $("#w_user").value.trim(), password = $("#w_pass").value;
    if (password !== $("#w_pass2").value) return msg("The two passwords don't match.");
    try { await api("/api/auth/account", { method: "PUT", body: { username, password } }); renderLogin(await api("/api/auth")); msg("Web login saved."); }
    catch (e) { msg(e.message); }
  };
  $("#w_remove").onclick = async () => {
    if (!confirm("Remove the web login? Remote access is turned off and everyone signed in with it is signed out.")) return;
    try { await api("/api/auth/account", { method: "DELETE" }); $("#s_remote").checked = false; $("#w_user").value = ""; renderLogin(await api("/api/auth")); msg("Web login removed."); }
    catch (e) { msg(e.message); }
  };
  $("#totpStart").onclick = async () => {
    try {
      const t = await api("/api/auth/totp/start", { method: "POST" });
      $("#totpQr").src = t.qr; $("#totpSecret").textContent = t.secret; $("#totpCode").value = "";
      $("#totpSetup").classList.remove("hidden"); $("#totpCode").focus();
    } catch (e) { msg(e.message); }
  };
  $("#totpConfirm").onclick = async () => {
    try {
      await api("/api/auth/totp/confirm", { method: "POST", body: { code: $("#totpCode").value } });
      renderLogin(await api("/api/auth"));
      msg("2FA is on. Sign-ins now need a code from your app.");
    }
    catch (e) { msg(e.message); }
  };
  $("#totpOff").onclick = async () => {
    if (!confirm("Turn off 2FA? Sign-ins will only need the password.")) return;
    try { await api("/api/auth/totp", { method: "DELETE" }); renderLogin(await api("/api/auth")); msg("2FA turned off."); }
    catch (e) { msg(e.message); }
  };
  $("#sessAll").onclick = async () => { renderSessions((await api("/api/auth/sessions", { method: "DELETE" })).list); msg("Everyone signed in with the web login was signed out."); };
}

// ------------------------------------------------------------------ pairing
let pairTimer = null;
async function openPair() {
  try {
    const p = await api("/api/pair/start", { method: "POST" });
    $("#pairQr").src = p.qr;
    $("#pairCode").textContent = p.code;
    $("#pairUrl").textContent = `${p.lan_url}/pair.html`;
    $("#pairWhere").textContent = p.remote ? "Works at home or away."
      : "Phone must be on your home Wi-Fi.";
    $("#caHint").classList.toggle("hidden", p.remote);
    $("#pairTypeHint").classList.toggle("hidden", p.remote);
    clearInterval(pairTimer);
    pairTimer = setInterval(() => {
      const left = Math.max(0, p.expires - Date.now() / 1000);
      $("#pairTtl").textContent = `${Math.floor(left / 60)}:${String(Math.floor(left % 60)).padStart(2, "0")}`;
      if (!left) { clearInterval(pairTimer); $("#pairCode").textContent = "expired"; }
    }, 500);
    $("#pairDlg").showModal();
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ views
const ago = (ts) => {
  if (!ts) return "";
  const s = Date.now() / 1000 - ts;
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  if (s < 86400 * 60) return `${Math.round(s / 86400)}d ago`;
  return new Date(ts * 1000).toLocaleDateString();
};
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
let newsLoaded = { yts: false, eztv: false }, newsTab = "yts", ytsPage = 1, eztvItems = [];
let eztvPage = 1, eztvShow = null, eztvSugg = [], eztvHasMore = false, ytsHasMore = false;

function switchView(v) {
  $$("#views button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  ["queue", "search", "news"].forEach((n) => $(`#view-${n}`).classList.toggle("hidden", n !== v));
  try { localStorage.setItem("rw_view", v); } catch {}
  if (v === "search") $("#searchQ").focus();
  if (v === "news" && !newsLoaded[newsTab]) loadNews(true);
  scrollTo({ top: 0 });
}

async function addMagnet(magnet, btn, label) {
  btn.disabled = true;
  try {
    await api("/api/jobs", { method: "POST", body: { text: magnet, mode: "auto", provider: $("#provider").value || null, sort: sortPref() } });
    btn.classList.add("added");
    btn.textContent = "✓ Queued";
    toast(`${label || "Torrent"} sent through the portal`);
    poll();
  } catch (e) { toast(e.message, true); btn.disabled = false; }
}
function copyBtn(magnet) {
  const b = el("button", "btn icon", "📋");
  b.title = "Copy magnet";
  b.onclick = async () => {
    try { await navigator.clipboard.writeText(magnet); toast("Magnet copied"); } catch { toast("Clipboard blocked", true); }
  };
  return b;
}
function addBtn(magnet, label, text = "➕ Add") {
  const b = el("button", "btn go", text);
  b.onclick = () => addMagnet(magnet, b, label);
  b.dataset.magnet = magnet;
  return b;
}
function loading(box) { box.replaceChildren(el("div", "portal spinner")); }
function notice(box, msg, bad = false) { box.replaceChildren(el("div", "notice" + (bad ? " bad" : ""), msg)); }

function resultRow(r, metaBits) {
  const row = el("article", "card result");
  const main = el("div", "r-main");
  main.append(el("div", "r-title", r.title));
  const meta = el("div", "r-meta");
  metaBits.filter(Boolean).forEach((m) => meta.append(m instanceof Node ? m : el("span", null, m)));
  meta.append(cacheTags(btih(r.magnet)));
  main.append(meta);
  const act = el("div", "r-act");
  act.append(copyBtn(r.magnet), addBtn(r.magnet, r.title));
  row.append(main, act);
  return row;
}

// --- search
async function doSearch(e) {
  e?.preventDefault();
  const q = $("#searchQ").value.trim();
  const box = $("#searchResults");
  if (!q) return;
  loading(box);
  try {
    const { results } = await api(`/api/search?q=${encodeURIComponent(q)}&sort=${$("#searchSort").value}&cat=${$("#searchCat").value}`);
    if (!results.length) return notice(box, `Nothing in this dimension for “${q}”. Try another universe.`);
    box.replaceChildren(...results.map((r) => resultRow(r, [
      r.size && `💾 ${r.size}`, r.category && `🏷 ${r.category}`, r.date && `🕒 ${ago(r.date)}`, "BT4G"])));
    autoCache("searchResults");
  } catch (err) { notice(box, `Aw jeez: ${err.message}`, true); }
}

// --- news
async function loadNews(reset) {
  newsLoaded[newsTab] = true;
  if (newsTab === "yts") return loadYts(reset);
  return loadEztv(reset);
}
async function loadYts(reset) {
  const grid = $("#ytsGrid");
  if (reset) { ytsPage = 1; loading(grid); }
  $("#ytsMore").classList.add("hidden");
  try {
    const q = encodeURIComponent($("#ytsQ").value.trim());
    const d = await api(`/api/news/yts?sort=${$("#ytsSort").value}&quality=${$("#ytsQuality").value}&page=${ytsPage}&q=${q}`);
    if (reset) grid.replaceChildren();
    if (!d.movies.length && reset) return notice(grid, "No movies found. Must be a Cronenberg world.");
    grid.append(...d.movies.map(posterCard));
    repaintCache();
    autoCache("news");
    ytsHasMore = d.more;
    $("#ytsMore").classList.toggle("hidden", !d.more || newsTab !== "yts");
  } catch (err) { notice(grid, `Aw jeez: ${err.message}`, true); }
}
function posterCard(m) {
  const card = el("article", "card poster");
  const cover = el("div", "cover");
  if (m.cover) {
    const img = el("img");
    img.loading = "lazy";
    img.alt = m.title;
    img.src = `/api/img?u=${encodeURIComponent(m.cover)}`;
    img.onerror = () => img.remove();
    cover.append(img);
  }
  if (m.rating) cover.append(el("span", "rating", `★ ${m.rating}`));
  const info = el("div", "info");
  info.append(el("b", null, `${m.title} (${m.year})`));
  info.append(el("div", "genres", [m.genres.slice(0, 3).join(" · "), m.runtime ? `${m.runtime} min` : "", ago(m.added)].filter(Boolean).join(" · ")));
  if (m.summary) info.title = m.summary;
  const qs = el("div", "qs");
  m.torrents.forEach((t) => {
    const b = addBtn(t.magnet, `${m.title} ${t.label}`, "");
    b.className = "btn";
    b.dataset.hash = btih(t.magnet) || "";
    b.append(`➕ ${t.label.replace(" web", "").replace(" bluray", " BR")} `, el("small", null, t.size));
    b.title = `${t.codec || ""} ${t.audio || ""}ch · ${t.seeds} seeds`;
    qs.append(b);
  });
  info.append(qs);
  card.append(cover, info);
  return card;
}
async function loadEztv(reset = true) {
  const box = $("#eztvList"), more = $("#eztvMore");
  eztvPage = reset ? 1 : eztvPage + 1;
  if (reset) { eztvItems = []; loading(box); }
  more.disabled = true;
  try {
    const d = await api(`/api/news/eztv?page=${eztvPage}&imdb=${eztvShow ? eztvShow.imdb : ""}`);
    eztvItems = eztvItems.concat(d.items);
    eztvHasMore = d.more;
    more.classList.toggle("hidden", !d.more);
    more.textContent = eztvShow ? `Older ${eztvShow.title} releases…` : "Older releases…";
    renderEztv();
  } catch (err) {
    if (reset) notice(box, `Aw jeez: ${err.message}`, true); else toast(err.message, true);
  } finally { more.disabled = false; }
}
function renderEztv() {
  const box = $("#eztvList");
  // with a show picked, the box filters its episodes (e.g. "S02"); otherwise it filters the loaded list
  const q = eztvShow ? $("#eztvQ").value.trim().toLowerCase() : "";
  const qual = $("#eztvQuality").value;
  const items = eztvItems.filter((i) => (!q || i.title.toLowerCase().includes(q)) && (!qual || i.title.includes(qual)));
  const chip = $("#eztvShow");
  chip.classList.toggle("hidden", !eztvShow);
  if (eztvShow) {
    chip.replaceChildren(el("span", null, `📺 ${eztvShow.title}${eztvShow.years ? ` (${eztvShow.years})` : ""}`),
      el("span", "hint", ` · ${eztvItems.length} loaded`));
    const x = el("button", "btn icon", "✕"); x.title = "Back to the latest releases";
    x.onclick = () => { eztvShow = null; $("#eztvQ").value = ""; $("#eztvQ").placeholder = "Search a show…"; loadEztv(true); };
    chip.append(x);
  }
  if (!items.length) return notice(box, eztvItems.length ? "Nothing matches that filter." : eztvShow ? "EZTV has nothing for that show." : "The feed is empty right now.");
  box.replaceChildren(...items.map((i) => {
    const seeds = el("span", "seeds" + (i.seeds ? "" : " dead"), `⬆ ${i.seeds} / ⬇ ${i.peers}`);
    return resultRow(i, [i.size_bytes && `💾 ${fmtBytes(i.size_bytes)}`, seeds, i.date && `🕒 ${ago(i.date)}`, "EZTV"]);
  }));
  autoCache("news");
}
// show search: IMDb suggestions -> pick one -> every EZTV release of that show
let suggTimer = null, suggSeq = 0;
function onEztvInput() {
  if (eztvShow) return renderEztv();
  clearTimeout(suggTimer);
  const q = $("#eztvQ").value.trim();
  if (q.length < 2) return hideSugg();
  suggTimer = setTimeout(async () => {
    const seq = ++suggSeq;
    try {
      const { shows } = await api(`/api/news/eztv/shows?q=${encodeURIComponent(q)}`);
      if (seq !== suggSeq) return;
      eztvSugg = shows;
      const box = $("#eztvSugg");
      if (!shows.length) { box.replaceChildren(el("div", "hint", "No TV shows by that name")); box.classList.remove("hidden"); return; }
      box.replaceChildren(...shows.map((sh, i) => {
        const b = el("button", i === 0 ? "on" : "");
        b.type = "button";
        b.append(el("b", null, sh.title), el("small", null, [sh.years, sh.cast].filter(Boolean).join(" · ")));
        b.onmousedown = (e) => { e.preventDefault(); pickShow(sh); };
        return b;
      }));
      box.classList.remove("hidden");
    } catch (err) { toast(err.message, true); }
  }, 300);
}
function hideSugg() { $("#eztvSugg").classList.add("hidden"); }
function pickShow(sh) {
  eztvShow = sh;
  $("#eztvQ").value = "";
  $("#eztvQ").placeholder = "Filter episodes (e.g. S02)…";
  hideSugg();
  loadEztv(true);
}
function setNewsTab(n) {
  newsTab = n;
  $$("#newsTabs button").forEach((b) => b.classList.toggle("on", b.dataset.n === n));
  $("#ytsTools").classList.toggle("hidden", n !== "yts");
  $("#eztvTools").classList.toggle("hidden", n !== "eztv");
  $("#ytsGrid").classList.toggle("hidden", n !== "yts");
  $("#ytsMore").classList.toggle("hidden", n !== "yts" || !ytsHasMore);
  $("#eztvList").classList.toggle("hidden", n !== "eztv");
  $("#eztvShow").classList.toggle("hidden", n !== "eztv" || !eztvShow);
  $("#eztvMore").classList.toggle("hidden", n !== "eztv" || !eztvHasMore);
  if (!newsLoaded[n]) loadNews(true);
}
function wireViews() {
  $$("#views button").forEach((b) => b.onclick = () => switchView(b.dataset.v));
  $("#searchForm").onsubmit = doSearch;
  $("#searchSort").onchange = $("#searchCat").onchange = () => $("#searchQ").value.trim() && doSearch();
  $$("#newsTabs button").forEach((b) => b.onclick = () => setNewsTab(b.dataset.n));
  $("#newsRefresh").onclick = () => loadNews(true);
  $("#ytsSort").onchange = $("#ytsQuality").onchange = () => loadYts(true);
  let t = null;
  $("#ytsQ").oninput = () => { clearTimeout(t); t = setTimeout(() => loadYts(true), 450); };
  $("#ytsMore").onclick = () => { ytsPage++; loadYts(false); };
  $("#eztvQ").oninput = onEztvInput;
  $("#eztvQ").onkeydown = (e) => {
    if (e.key === "Enter" && !eztvShow && eztvSugg.length && !$("#eztvSugg").classList.contains("hidden")) pickShow(eztvSugg[0]);
    if (e.key === "Escape") hideSugg();
  };
  $("#eztvQ").onblur = () => setTimeout(hideSugg, 150);
  $("#eztvQuality").onchange = renderEztv;
  $("#eztvMore").onclick = () => loadEztv(false);
  let v = "queue";
  try { v = localStorage.getItem("rw_view") || "queue"; } catch {}
  if (v !== "queue") switchView(v);
}

// ------------------------------------------------------------------ wiring
function wire() {
  wireViews();
  $("#addBtn").onclick = addLinks;
  $("#links").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) addLinks(); });
  $("#torrentBtn").onclick = () => $("#torrentFile").click();
  $("#torrentFile").onchange = (e) => { uploadTorrents(e.target.files); e.target.value = ""; };
  $("#clearBtn").onclick = async () => { await api("/api/jobs/clear", { method: "POST" }); poll(); };
  $("#retryBtn").onclick = async () => {
    try { const r = await api("/api/jobs/retry", { method: "POST" }); toast(`Retrying ${r.retried} download${r.retried === 1 ? "" : "s"}`); poll(); }
    catch (e) { toast(e.message, true); }
  };
  $$(".cacheBtn").forEach((b) => b.onclick = () => runCacheCheck(b.dataset.for, false, b));
  wireFiles();
  wireSortPref();
  $$("#tabs button").forEach((b) => b.onclick = () => {
    filter = b.dataset.f; $$("#tabs button").forEach((x) => x.classList.toggle("on", x === b)); poll();
  });
  $$("[data-close]").forEach((b) => b.onclick = () => b.closest("dialog").close());
  $("#pairDlg").addEventListener("close", () => clearInterval(pairTimer));
  if (me.admin) {
    $("#settingsBtn").onclick = () => openSettings().catch((e) => toast(e.message, true));
    $("#pairBtn").onclick = openPair;
    $("#saveSet").onclick = saveSettings;
    $("#pickDir").onclick = async () => { const r = await api("/api/system/pick-folder", { method: "POST" }); if (r.path) $("#s_dir").value = r.path; };
    $("#assocOn").onclick = async () => renderAssoc(await api("/api/system/associate?on=true", { method: "POST" }));
    $("#assocOff").onclick = async () => renderAssoc(await api("/api/system/associate?on=false", { method: "POST" }));
    $("#defApps").onclick = () => api("/api/system/default-apps", { method: "POST" });
    wireSetup();
    wireLogin();
  }
  // drag & drop .torrent / links
  let depth = 0;
  addEventListener("dragenter", (e) => { e.preventDefault(); depth++; document.body.classList.add("dragging"); });
  addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; document.body.classList.remove("dragging"); } });
  addEventListener("dragover", (e) => e.preventDefault());
  addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; document.body.classList.remove("dragging");
    if (e.dataTransfer.files.length) uploadTorrents(e.dataTransfer.files);
    else {
      const t = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
      if (t) { $("#links").value = ($("#links").value + "\n" + t).trim(); }
    }
  });
  // paste anywhere (outside inputs) drops links into the box
  addEventListener("paste", (e) => {
    if (e.target.closest("input, textarea")) return;
    const t = e.clipboardData.getData("text");
    if (t) { $("#links").value = ($("#links").value + "\n" + t).trim(); $("#links").focus(); }
  });
}

// ------------------------------------------------------------------ media tools
let toolTimer = null;
const TOOL_SRC = { rickwire: "RickWire's copy", system: "found on this PC", "built in": "built into RickWire" };
function toolRow(t) {
  const d = el("div", "tool");
  const name = el("div", "name", t.label);
  const what = el("div", "what", t.what);
  let ver;
  if (t.version) ver = el("div", "ver ok-text", `✓ ${t.version} · ${TOOL_SRC[t.source] || t.source}`);
  else if (t.alt) ver = el("div", "ver ok-text", `✓ using ${t.alt} (Deno optional)`);
  else ver = el("div", "ver bad-text", "✗ not installed");
  if (t.pending) ver.append(el("span", "hint", ` · ${t.pending} downloaded, restart to use it`));
  if (t.latest && t.update && !t.pending) ver.append(el("span", "hint", ` · ${t.latest} available`));
  if (t.error) ver.append(el("span", "bad-text", ` · check failed: ${t.error}`));
  const act = el("div", "act");
  const job = t.job || {};
  const busy = ["downloading", "verifying", "installing"].includes(job.state);
  if (busy) {
    const pct = job.total ? Math.round((job.done / job.total) * 100) : 0;
    act.append(el("span", "hint", job.state === "downloading" ? `${pct}% of ${fmtBytes(job.total)}` : job.state === "verifying" ? "checking SHA-256…" : "installing…"));
    const bar = el("div", "progress"); const i = document.createElement("i"); i.style.width = `${job.state === "downloading" ? pct : 100}%`; bar.append(i);
    d.append(name, what, ver, act, bar);
    return d;
  }
  if (job.state === "error") ver.append(el("span", "bad-text", ` · ${job.error}`));
  if (t.missing || t.update) {
    const b = el("button", "btn" + (t.missing ? " go" : ""), t.missing ? `⬇ Install (${t.size})` : `⬆ Update to ${t.latest}`);
    b.onclick = () => installTool(t.name);
    act.append(b);
  } else if (t.alt) act.append(el("span", "hint", "optional"));
  else if (t.latest) act.append(el("span", "hint ok-text", "up to date"));
  d.append(name, what, ver, act);
  return d;
}
function renderTools(st) {
  for (const box of [$("#toolList"), $("#wzTools")]) box.replaceChildren(...st.tools.map(toolRow));
  $("#toolRestart").classList.toggle("hidden", !st.restart_needed);
  $("#toolChecked").textContent = st.checked_at ? `checked ${new Date(st.checked_at * 1000).toLocaleTimeString()}` : "";
  const missing = st.tools.filter((t) => t.missing || (t.update && t.name !== "yt-dlp")).length;
  $("#wzToolsGet").classList.toggle("hidden", !st.tools.some((t) => t.missing));
  $("#wzToolsGet").disabled = st.busy;
  $("#wzToolsMsg").textContent = st.busy ? "Downloading…" : st.tools.some((t) => t.missing) ? "" : "✓ Everything's ready.";
  clearTimeout(toolTimer);
  if (st.busy) toolTimer = setTimeout(refreshTools, 700);
  return missing;
}
async function refreshTools() {
  try { return renderTools(await api("/api/tools")); } catch (e) { toast(e.message, true); }
}
async function checkTools() {
  const b = $("#toolCheck"); b.disabled = true; b.textContent = "Checking…";
  try { renderTools(await api("/api/tools/check", { method: "POST" })); }
  catch (e) { toast(e.message, true); }
  finally { b.disabled = false; b.textContent = "⟳ Check for updates"; }
}
async function installTool(name) {
  try { renderTools(await api(`/api/tools/${name}/install`, { method: "POST" })); }
  catch (e) { toast(e.message, true); }
}
async function restartApp() {
  try { await api("/api/system/restart", { method: "POST" }); toast("Restarting…"); }
  catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------------ first-run setup
const WZ_STEPS = ["welcome", "provider", "tools", "done"];
const WZ_TITLES = { welcome: "Welcome to RickWire", provider: "Step 1 · Debrid service", tools: "Step 2 · Media tools", done: "Ready" };
const PROV_BLURB = {
  realdebrid: "The most widely used. Huge list of hosters.",
  alldebrid: "Lots of hosters and fast torrent caching.",
  torbox: "Torrent-focused, has a limited free plan.",
  premiumize: "Includes cloud storage and Usenet.",
};
let wzStep = 0, wzPick = null;
async function openSetup() {
  wzStep = 0; wzPick = null;
  await loadProviders();
  wzRender();
  $("#setupDlg").showModal();
  api("/api/tools/check", { method: "POST" }).then(renderTools).catch(() => refreshTools());
}
function wzRender() {
  const step = WZ_STEPS[wzStep];
  $$("#setupDlg .wz").forEach((s) => s.classList.toggle("hidden", s.dataset.step !== step));
  $("#wzTitle").textContent = WZ_TITLES[step];
  $("#wzStep").textContent = wzStep && wzStep < 3 ? `${wzStep} of 2` : "";
  $("#wzBack").classList.toggle("hidden", wzStep === 0);
  $("#wzSkip").classList.toggle("hidden", step === "done");
  const configured = providers.providers.filter((p) => p.configured);
  $("#wzNext").textContent = step === "done" ? "Let's go! ➜" : step === "provider" && !configured.length ? "Skip for now →" : "Next →";
  if (step === "provider") wzProviders();
  if (step === "done") wzSummary();
}
function wzProviders() {
  $("#wzProvs").replaceChildren(...providers.providers.map((p) => {
    const b = el("button", p.id === wzPick ? "on" : "");
    b.type = "button";
    const title = el("b", "", p.name);
    if (p.configured) title.append(el("span", "tick", " ✓"));
    b.append(title, el("small", "", PROV_BLURB[p.id] || ""));
    b.onclick = () => { wzPick = p.id; wzProviders(); };
    return b;
  }));
  const p = providers.providers.find((x) => x.id === wzPick);
  if (!p) { $("#wzProvCard").replaceChildren(); return; }
  const card = provRow(p);
  card.querySelector(".en")?.closest("label")?.remove();
  // refresh the tiles and the Next button once a key is saved
  card.querySelector(".save").addEventListener("click", () => setTimeout(async () => { await loadProviders(); wzRender(); }, 1200));
  $("#wzProvCard").replaceChildren(card);
  card._autotest && card._autotest();
}
async function wzSummary() {
  const configured = providers.providers.filter((p) => p.configured).map((p) => p.name);
  let tools = null;
  try { tools = await api("/api/tools"); } catch {}
  const items = [
    configured.length ? `✓ Debrid: ${configured.join(", ")}` : "⏭ No debrid service yet. Torrents and hoster links will wait until you add one in ⚙ Settings.",
  ];
  if (tools) {
    const missing = tools.tools.filter((t) => t.missing).map((t) => t.label);
    items.push(missing.length ? `⏭ Not installed: ${missing.join(", ")} (Settings › Media tools)` : "✓ Media tools ready (YouTube & co.)");
  }
  $("#wzSummary").replaceChildren(...items.map((t) => el("li", "", t)));
}
async function wzFinish(skipped) {
  try {
    if (!skipped && $("#wzAssoc").checked) await api("/api/system/associate?on=true", { method: "POST" });
    await api("/api/settings", { method: "PUT", body: { onboarded: true } });
  } catch (e) { toast(e.message, true); }
  $("#setupDlg").close();
  await loadProviders();
  if (skipped) toast("Setup skipped. You can run it again from ⚙ Settings › Window.");
}
function wireSetup() {
  $("#wzNext").onclick = () => { if (wzStep === WZ_STEPS.length - 1) return wzFinish(false); wzStep++; wzRender(); };
  $("#wzBack").onclick = () => { wzStep = Math.max(0, wzStep - 1); wzRender(); };
  $("#wzSkip").onclick = () => wzFinish(true);
  $("#wzToolsGet").onclick = async () => {
    const st = await api("/api/tools");
    for (const t of st.tools.filter((x) => x.missing)) await installTool(t.name);
  };
  $("#setupDlg").addEventListener("cancel", (e) => e.preventDefault());   // Esc doesn't silently skip
  $("#toolCheck").onclick = checkTools;
  $("#toolRestart").onclick = restartApp;
  $("#rerunSetup").onclick = () => { $("#setDlg").close(); openSetup(); };
}

boot();
