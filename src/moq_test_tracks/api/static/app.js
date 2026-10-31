// Copyright (c) OpenMOQ contributors.
// This source code is licensed under the Apache 2.0 license found in the
// LICENSE file in the root directory of this source tree.

// Dashboard. All text from the API is set with textContent, never innerHTML.

"use strict";

const $ = (id) => document.getElementById(id);
const FIELDS = ["content", "cat", "pkg", "vc", "h", "fps", "ac", "ch", "init", "gop", "adel", "dur", "vt"];
const ACTIVE = ["requested", "checked", "starting", "running", "stopping"];
let me = null;
let events = null;
let timer = null;

async function api(method, path, body) {
  const opts = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const json = (r.headers.get("content-type") || "").includes("json") ? await r.json() : null;
  if (!r.ok) {
    const errors = (json && ((json.detail && json.detail.errors) || json.errors)) || [r.statusText];
    throw new Error(Array.isArray(errors) ? errors.join("; ") : String(errors));
  }
  return json;
}

function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}

function params() {
  const p = {};
  for (const f of FIELDS) {
    const v = $(f).value.trim();
    if (v) p[f] = v;
  }
  return p;
}

function jobRequest() {
  const ttl = $("ttl").value.trim();
  return {
    preset: $("preset").value || undefined,
    clip: $("clip").value || undefined,
    params: params(),
    tool: $("tool").value,
    relay: $("relay").value,
    transport: $("transport").value,
    mode: $("mode").value,
    ttl_s: ttl ? Number(ttl) : undefined,
  };
}

async function preview() {
  const q = new URLSearchParams({ ...params(), tool: $("tool").value });
  if ($("preset").value) q.set("preset", $("preset").value);
  if ($("clip").value) q.set("clip", $("clip").value);
  $("plan").hidden = true;
  try {
    const r = await api("GET", "/api/v1/namespace?" + q);
    $("ns").textContent = r.namespace;
    $("ns-safe").textContent = r.namespace_safe;
    $("job-error").textContent = "";
  } catch (e) {
    $("ns").textContent = "";
    $("ns-safe").textContent = "";
    $("job-error").textContent = e.message;
  }
}

async function check() {
  try {
    const r = await api("POST", "/api/v1/jobs/check", jobRequest());
    let text = r.plan.join(" ");
    if (r.joins) text += "\n(joins running job " + r.joins + ")";
    $("plan").textContent = text;
    $("plan").hidden = false;
    $("job-error").textContent = "";
  } catch (e) {
    $("plan").hidden = true;
    $("job-error").textContent = e.message;
  }
}

async function start(ev) {
  ev.preventDefault();
  try {
    const job = await api("POST", "/api/v1/jobs", jobRequest());
    $("job-error").textContent = job.joined ? "Joined running job " + job.id : "";
    await refresh();
  } catch (e) {
    $("job-error").textContent = e.message;
  }
}

function stats(job) {
  const s = job.last_stats;
  if (!s || !s.tracks) return "";
  return Object.entries(s.tracks)
    .filter(([name]) => name !== "catalog")
    .map(([name, t]) => `${name} ${Math.round(t.objects_per_s || 0)}/s ${Math.round(t.kbps || 0)}k` + (t.dropped ? ` drop ${t.dropped}` : ""))
    .join(" · ");
}

function expires(job) {
  if (!ACTIVE.includes(job.state)) return "";
  const s = Math.max(0, Math.round(job.expires - Date.now() / 1000));
  return s > 3600 ? `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m${s % 60}s`;
}

function row(job) {
  const tr = el("tr");
  tr.append(el("td", job.state, "state " + job.state));
  const ns = el("td");
  ns.append(el("code", job.namespace));
  ns.title = job.namespace_safe;
  tr.append(ns, el("td", job.tool), el("td", job.relay), el("td", job.owner_name || ""));
  tr.append(el("td", expires(job)), el("td", stats(job)));
  const actions = el("td", undefined, "actions");
  const log = el("button", "Log");
  log.addEventListener("click", () => showLog(job));
  const copy = el("button", "Copy");
  copy.addEventListener("click", () => navigator.clipboard.writeText(job.namespace));
  actions.append(log, copy);
  const mine = me && (job.owner === me.token_id || me.role === "admin");
  if (ACTIVE.includes(job.state) && mine && me.role !== "view") {
    const stop = el("button", "Stop");
    stop.addEventListener("click", () => stopJob(job.id, false));
    const kill = el("button", "Kill", "danger");
    kill.addEventListener("click", () => stopJob(job.id, true));
    actions.append(stop, kill);
  }
  tr.append(actions);
  return tr;
}

async function refresh() {
  try {
    const jobs = await api("GET", "/api/v1/jobs?active=" + $("active-only").checked);
    $("job-rows").replaceChildren(...jobs.map(row));
  } catch (e) {
    if (String(e.message).includes("authentication")) showLogin();
  }
}

async function stopJob(id, force) {
  try {
    await api("DELETE", `/api/v1/jobs/${encodeURIComponent(id)}?force=${force}`);
  } catch (e) {
    alert(e.message);
  }
  refresh();
}

function showLog(job) {
  if (events) events.close();
  $("log-panel").hidden = false;
  $("log-title").textContent = job.id + " " + job.namespace;
  const out = $("log");
  out.textContent = "";
  const append = (line) => {
    out.textContent += line + "\n";
    out.scrollTop = out.scrollHeight;
  };
  events = new EventSource(`/api/v1/jobs/${encodeURIComponent(job.id)}/events`);
  events.addEventListener("state", (e) => append("state: " + JSON.parse(e.data).state));
  events.addEventListener("event", (e) => {
    const ev = JSON.parse(e.data);
    if (ev.event !== "stats") append(ev.event + ": " + JSON.stringify(ev));
  });
  events.addEventListener("log", (e) => append(JSON.parse(e.data)));
  events.onerror = () => {
    events.close();
    append("(stream closed)");
  };
}

function showLogin() {
  me = null;
  clearInterval(timer);
  $("app").hidden = true;
  $("logout").hidden = true;
  $("who").textContent = "";
  $("login").hidden = false;
}

async function showApp() {
  $("login").hidden = true;
  $("app").hidden = false;
  $("logout").hidden = false;
  const expires = new Date(me.expires * 1000).toISOString().slice(0, 10);
  $("who").textContent = `${me.name} (${me.role}, ${me.tier})`;
  $("who").title = `token ${me.token_id}; relays ${me.relays.join(", ") || "none"}; expires ${expires}`;
  $("stop-all").hidden = me.role !== "admin";
  const [tools, relays, presets, clips] = await Promise.all([
    api("GET", "/api/v1/tools"),
    api("GET", "/api/v1/relays"),
    api("GET", "/api/v1/presets"),
    api("GET", "/api/v1/clips").catch(() => []),
  ]);
  $("tool").replaceChildren(...tools.map((t) => new Option(`${t.name} (${t.version})`, t.id)));
  $("relay").replaceChildren(...relays.relays.map((r) => new Option(r.name, r.id, false, r.id === relays.default)));
  $("preset").replaceChildren(new Option("(none)", ""), ...Object.keys(presets.presets).map((p) => new Option(p, p)));
  $("clip").replaceChildren(
    new Option("(none)", ""),
    ...clips.map((c) => {
      const opt = new Option(c.fetched ? c.title : `${c.title} (not fetched)`, c.id);
      opt.disabled = !c.fetched;
      opt.title = `${c.attribution}. ${c.license}`;
      return opt;
    }),
  );
  preview();
  refresh();
  timer = setInterval(refresh, 3000);
}

async function init() {
  try {
    me = await api("GET", "/api/v1/me");
    await showApp();
  } catch {
    showLogin();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $("login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      me = await api("POST", "/login", { token: $("token").value });
      $("token").value = "";
      $("login-error").textContent = "";
      await showApp();
    } catch (e) {
      $("login-error").textContent = e.message;
    }
  });
  $("logout").addEventListener("click", async () => {
    await api("POST", "/logout");
    showLogin();
  });
  $("job-form").addEventListener("submit", start);
  $("job-form").addEventListener("change", preview);
  $("check").addEventListener("click", check);
  $("copy-ns").addEventListener("click", () => navigator.clipboard.writeText($("ns").textContent));
  $("active-only").addEventListener("change", refresh);
  $("log-close").addEventListener("click", () => {
    if (events) events.close();
    $("log-panel").hidden = true;
  });
  $("stop-all").addEventListener("click", async () => {
    if (!confirm("Stop every job?")) return;
    await api("POST", "/api/v1/admin/stop-all");
    refresh();
  });
  init();
});
