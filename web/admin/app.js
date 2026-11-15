// RADIO STATION STUDIO PRO — painel administrativo (SPA sem build, módulos ES)
// Token de acesso apenas em memória; sessão renovada por cookie httpOnly de refresh.

const S = { token: null, user: null, stations: [], sid: null, meta: null, timers: [], tzOffset: 0 };
const $app = document.getElementById("app");

// ------------------------------------------------------------------ utilidades
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "html") n.innerHTML = v; // usado apenas com SVG estático do próprio código
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
const ICON = {
  logo: `<svg width="24" height="24" viewBox="0 0 32 32"><path d="M8 21a10 10 0 0 1 16 0M11.5 18a5.5 5.5 0 0 1 9 0" stroke="#19d3e6" stroke-width="2.4" fill="none" stroke-linecap="round"/><circle cx="16" cy="22.5" r="2.2" fill="#ff3b4e"/></svg>`,
};
const icon = (name) => el("span", { class: "brand-mark", html: ICON[name] });
function toast(msg, kind = "") {
  const t = el("div", { class: `toast ${kind}` }, msg);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), 4500);
}
const fmtDur = (ms) => {
  if (ms === null || ms === undefined) return "—";
  const s = Math.max(0, Math.round(ms / 1000)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(x).padStart(2, "0")}`;
};
const fmtBytes = (b) => { if (b == null) return "—"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < 4) { b /= 1024; i++; } return `${b.toFixed(i ? 1 : 0)} ${u[i]}`; };
const tz = () => (station()?.timezone) || "America/Sao_Paulo";
const fmtDate = (iso) => iso ? new Date(iso).toLocaleString("pt-BR", { timeZone: tz() }) : "—";
const fmtTime = (iso) => iso ? new Date(iso).toLocaleTimeString("pt-BR", { timeZone: tz() }) : "—";
const station = () => S.stations.find((s) => s.id === S.sid);
const can = (perm) => S.user?.is_superadmin || (station()?.permissions || []).includes(perm);
const clearTimers = () => { S.timers.forEach(clearInterval); S.timers = []; };

// ------------------------------------------------------------------ API
async function api(path, opts = {}, retry = true) {
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers.Authorization = `Bearer ${S.token}`;
  let body = opts.body;
  if (body && !(body instanceof FormData) && typeof body !== "string") { body = JSON.stringify(body); headers["Content-Type"] = "application/json"; }
  const r = await fetch(path, { method: opts.method || "GET", headers, body, credentials: "same-origin" });
  if (r.status === 401 && retry && !path.startsWith("/api/auth/")) {
    if (await refresh()) return api(path, opts, false);
    showLogin(); throw new Error("sessão expirada");
  }
  if (opts.raw) return r;
  const data = r.headers.get("content-type")?.includes("json") ? await r.json() : await r.text();
  if (!r.ok) { const e = new Error(data?.error || `erro ${r.status}`); e.status = r.status; e.data = data; throw e; }
  return data;
}
async function refresh() {
  try {
    const r = await fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" });
    if (!r.ok) return false;
    const d = await r.json(); S.token = d.access_token; S.user = d.user; return true;
  } catch { return false; }
}
setInterval(() => { if (S.token) refresh(); }, 10 * 60 * 1000);

// ------------------------------------------------------------------ diálogos
function modal(title, body, actions = []) {
  return new Promise((resolve) => {
    const close = (v) => { bg.remove(); resolve(v); };
    const bg = el("div", { class: "modal-bg", onclick: (e) => { if (e.target === bg) close(null); } },
      el("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title },
        el("h3", {}, title), body,
        el("div", { class: "actions" }, actions.map((a) => el("button", { class: `btn ${a.class || ""}`, onclick: async () => {
          if (a.handler) { try { const v = await a.handler(); if (v !== false) close(v ?? a.value); } catch (e) { toast(e.message, "err"); } }
          else close(a.value);
        } }, a.label)))));
    document.body.append(bg);
    bg.addEventListener("keydown", (e) => { if (e.key === "Escape") close(null); });
    setTimeout(() => (bg.querySelector("input,select,textarea,button.primary") || bg.querySelector("button"))?.focus(), 30);
  });
}
const confirmBox = (title, text, danger = false) => modal(title, el("p", {}, text),
  [{ label: "Cancelar", value: false }, { label: "Confirmar", value: true, class: danger ? "danger solid" : "primary" }]);

// ------------------------------------------------------------------ login
function showLogin() {
  clearTimers(); S.token = null;
  const err = el("div", { class: "err", role: "alert" });
  const email = el("input", { type: "email", id: "email", autocomplete: "username", required: true });
  const pass = el("input", { type: "password", id: "pass", autocomplete: "current-password", required: true });
  const form = el("form", { onsubmit: async (e) => {
    e.preventDefault(); err.textContent = "";
    try {
      const d = await api("/api/auth/login", { method: "POST", body: { email: email.value, password: pass.value } });
      if (d.mfa_required) return showMfa(d.challenge);
      S.token = d.access_token; boot();
    } catch (ex) { err.textContent = ex.message; }
  } },
    el("div", { class: "field" }, el("label", { for: "email" }, "E-mail"), email),
    el("div", { class: "field" }, el("label", { for: "pass" }, "Senha"), pass),
    err, el("button", { class: "btn primary", style: "width:100%;justify-content:center", type: "submit" }, "Entrar"));
  $app.replaceChildren(el("div", { class: "login-wrap" }, el("div", { class: "login-card" },
    el("div", { class: "brand" }, icon("logo"), el("div", {}, el("h1", {}, "RADIO STATION STUDIO PRO"), el("small", {}, "EmpreenderTHZ"))),
    el("p", { class: "slogan" }, "Sua rádio. Sua programação. Seu universo sonoro."), form)));
  email.focus();
}
function showMfa(challenge) {
  const err = el("div", { class: "err", role: "alert" });
  const code = el("input", { id: "code", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, pattern: "[0-9]*" });
  $app.replaceChildren(el("div", { class: "login-wrap" }, el("div", { class: "login-card" },
    el("div", { class: "brand" }, icon("logo"), el("div", {}, el("h1", {}, "Verificação em duas etapas"), el("small", {}, "Código do aplicativo autenticador"))),
    el("form", { onsubmit: async (e) => {
      e.preventDefault();
      try { const d = await api("/api/auth/mfa", { method: "POST", body: { challenge, code: code.value } }); S.token = d.access_token; boot(); }
      catch (ex) { err.textContent = ex.message; }
    } }, el("div", { class: "field" }, el("label", { for: "code" }, "Código de 6 dígitos"), code), err,
      el("button", { class: "btn primary", type: "submit" }, "Verificar"), " ",
      el("button", { class: "btn ghost", type: "button", onclick: showLogin }, "Voltar")))));
  code.focus();
}

// ------------------------------------------------------------------ shell
const NAV = [
  ["Operação", [["dashboard", "Dashboard", "station.view"], ["queue", "Fila de execução", "queue.view"], ["library", "Biblioteca", "library.view"], ["stream", "Transmissão", "stream.view"]]],
  ["Gestão", [["reports", "Relatórios", "reports.view"], ["team", "Equipe", "station.view"], ["audit", "Auditoria", "audit.view"], ["station", "Emissora", "station.edit"]]],
];
const SOON = [["studio", "Estúdio ao vivo"], ["automation", "Automação / grade"], ["playlists", "Playlists inteligentes"],
  ["podcast", "Podcast Studio"], ["ads", "Publicidade"], ["portal", "Portal público"], ["analytics", "Analytics"],
  ["ai", "Inteligência artificial"], ["mobile", "App mobile / PWA"], ["saas", "Planos e assinaturas"]];

async function boot() {
  try {
    const me = await api("/api/me");
    S.user = me.user; S.stations = me.stations;
    S.meta = S.meta || await api("/api/meta");
    const saved = localStorageGet("rssp.sid");
    S.sid = S.stations.find((s) => s.id === saved)?.id || S.stations[0]?.id || null;
    render();
  } catch (e) { if (e.status !== 401) toast(e.message, "err"); }
}
function localStorageGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function localStorageSet(k, v) { try { localStorage.setItem(k, v); } catch { /* indisponível */ } }

let onairEl;
function render() {
  const route = (location.hash.replace(/^#\/?/, "") || "dashboard").split("/");
  const side = el("aside", { class: "side", id: "side" },
    el("div", { class: "brand" }, icon("logo"), el("div", {}, el("h1", {}, "RSSP"), el("small", {}, "Radio Station Studio Pro"))));
  const link = (id, label, extra) => el("a", { href: `#/${id}`, class: (route[0] === id || (route[0] === "module" && route[1] === id) ? "active " : "") + (extra ? "soon" : ""),
    onclick: () => side.classList.remove("open") }, label, extra || null);
  if (S.sid) for (const [h, items] of NAV) {
    const vis = items.filter(([, , p]) => can(p));
    if (vis.length) side.append(el("div", { class: "nav-h" }, h), el("nav", { class: "nav" }, vis.map(([id, label]) => link(id, label))));
  }
  if (S.user.is_superadmin) side.append(el("div", { class: "nav-h" }, "Plataforma"), el("nav", { class: "nav" }, link("admin-stations", "Emissoras"), link("admin-users", "Usuários"), link("modules", "Estado dos módulos")));
  side.append(el("div", { class: "nav-h" }, "Próximas fases"), el("nav", { class: "nav" }, SOON.map(([id, label]) => {
    const m = S.meta?.modules.find((x) => x.id === id);
    return link(`module/${id}`, label, el("span", { class: "badge" }, m?.status === "partial" ? "parcial" : `Fase ${m?.phase ?? "?"}`));
  })));
  side.append(el("div", { style: "flex:1" }), el("nav", { class: "nav" }, link("account", "Minha conta")));

  const sel = el("select", { "aria-label": "Emissora", onchange: (e) => { S.sid = e.target.value; localStorageSet("rssp.sid", S.sid); render(); } },
    S.stations.map((s) => el("option", { value: s.id, selected: s.id === S.sid }, s.name)));
  onairEl = el("span", { class: "onair", title: "Estado do ar" }, "OFF AIR");
  const top = el("header", { class: "topbar" },
    el("button", { class: "btn icon menu-btn", "aria-label": "Menu", onclick: () => side.classList.toggle("open") }, "☰"),
    S.stations.length ? sel : el("span", { class: "na" }, "Nenhuma emissora"), onairEl, el("div", { class: "spacer" }),
    el("button", { class: "btn ghost sm", title: "Alternar tema claro/escuro", "aria-label": "Alternar tema claro/escuro", onclick: toggleTheme },
      el("span", { html: '<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 1.5a6.5 6.5 0 0 1 0 13z" fill="currentColor"/></svg>', style: "display:inline-flex" }), "Tema"),
    el("span", { class: "hide-sm", style: "color:var(--muted)" }, S.user.name),
    el("button", { class: "btn sm", onclick: logout }, "Sair"));
  const content = el("section", { class: "content", id: "content" });
  $app.replaceChildren(el("div", { class: "shell" }, side, el("main", { class: "main" }, top, content)));
  clearTimers();
  const views = { dashboard: viewDashboard, queue: viewQueue, library: viewLibrary, stream: viewStream, reports: viewReports,
    team: viewTeam, audit: viewAudit, station: viewStation, account: viewAccount, "admin-stations": viewAdminStations,
    "admin-users": viewAdminUsers, modules: viewModules, module: viewModule };
  const fn = views[route[0]] || viewDashboard;
  if (!S.sid && !["account", "admin-stations", "admin-users", "modules", "module"].includes(route[0])) {
    content.append(el("div", { class: "card" }, el("h3", {}, "Sem emissoras"), el("p", {}, S.user.is_superadmin ? "Crie a primeira emissora em Plataforma → Emissoras." : "Peça ao proprietário de uma emissora para incluir você na equipe.")));
  } else fn(content, route.slice(1)).catch((e) => content.append(el("div", { class: "alert critical" }, e.message)));
  pollOnAir();
}
window.addEventListener("hashchange", () => S.user && render());
function toggleTheme() { const r = document.documentElement; r.dataset.theme = r.dataset.theme === "light" ? "dark" : "light"; localStorageSet("rssp.theme", r.dataset.theme); }
document.documentElement.dataset.theme = localStorageGet("rssp.theme") || "dark";
async function logout() { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); S.token = null; S.user = null; showLogin(); }
async function pollOnAir() {
  if (!S.sid) return;
  const tick = async () => {
    try { const s = await api(`/api/stations/${S.sid}/status`); setOnAir(s); } catch { /* silencioso */ }
  };
  tick(); S.timers.push(setInterval(tick, 5000));
}
function setOnAir(s) {
  if (!onairEl) return;
  const live = s.status === "on_air";
  onairEl.className = "onair" + (live ? " live" : (s.stream_enabled ? " warn" : ""));
  onairEl.textContent = live ? "ON AIR" : ({ dead_air: "SEM ÁUDIO", connecting: "CONECTANDO", stopped: "OFF AIR", offline: s.stream_enabled ? "PLAYOUT OFFLINE" : "OFF AIR", waiting_config: "SEM CONFIG." }[s.status] || "OFF AIR");
}
const pageH = (title, ...right) => el("div", { class: "page-h" }, el("h2", {}, title), el("div", { class: "spacer" }), ...right);

// ------------------------------------------------------------------ dashboard
async function viewDashboard(c) {
  const sid = S.sid;
  const cover = el("div", { class: "cover" }, el("span", { html: ICON.logo }));
  const title = el("div", { class: "title" }, "—"), artist = el("div", { class: "artist" });
  const bar = el("i"), elapsed = el("span"), remain = el("span");
  const srcB = el("span", { class: "badge" }), eq = el("span", { class: "eq paused" }, el("i"), el("i"), el("i"), el("i"));
  const nextEl = el("div", { class: "sub" });
  const tiles = el("div", { class: "tiles" }), alerts = el("div", { class: "alerts" }), q = el("ol", { class: "qlist" });
  const health = el("div");
  const btnStart = el("button", { class: "btn primary", onclick: () => streamToggle(true) }, "▶ Iniciar transmissão");
  const btnStop = el("button", { class: "btn danger", onclick: () => streamToggle(false) }, "■ Parar transmissão");
  const btnSkip = el("button", { class: "btn", onclick: async () => { await api(`/api/stations/${sid}/playout/skip`, { method: "POST" }); toast("Comando de pular enviado ao playout.", "ok"); } }, "⏭ Pular faixa");
  const pub = el("a", { class: "btn ghost", href: `/r/${station().slug}`, target: "_blank", rel: "noopener" }, "Abrir player público ↗");
  const ctr = el("div", { class: "controls" }, can("stream.control") ? [btnStart, btnStop] : null, can("playout.skip") ? btnSkip : null, pub,
    el("button", { class: "btn", disabled: true, title: "Indisponível — Fase 2" }, "Inserir vinheta (Fase 2)"),
    el("button", { class: "btn", disabled: true, title: "Indisponível — Fase 2" }, "Emergência (Fase 2)"));
  c.append(pageH("Dashboard", el("span", { class: "status-pill" }, el("span", { class: "dot", id: "hb" }), el("span", { id: "hbt" }, "…"))),
    el("div", { class: "grid g-dash" },
      el("div", { class: "grid" },
        el("div", { class: "card" }, el("h3", {}, "No ar agora"),
          el("div", { class: "now" }, cover, el("div", {}, el("div", { style: "display:flex;gap:8px;align-items:center;margin-bottom:6px" }, eq, srcB), title, artist)),
          el("div", { class: "progress" }, bar), el("div", { class: "times" }, elapsed, remain),
          el("div", { style: "margin-top:10px" }, el("b", {}, "Próximo: "), nextEl), ctr),
        tiles, el("div", { class: "card" }, el("h3", {}, "Saúde da transmissão"), health)),
      el("div", { class: "grid", style: "align-content:start" },
        el("div", { class: "card" }, el("h3", {}, "Alertas"), alerts),
        el("div", { class: "card" }, el("h3", {}, "Fila (próximos)"), q, el("a", { href: "#/queue" }, "Gerenciar fila →")))));

  let last = null;
  async function streamToggle(on) {
    if (!on && !(await confirmBox("Parar transmissão", "Os ouvintes serão desconectados e o mountpoint ficará livre. Confirmar?", true))) return;
    try { await api(`/api/stations/${sid}/stream/${on ? "start" : "stop"}`, { method: "POST" }); toast(on ? "Transmissão ativada; o playout conecta em instantes." : "Transmissão desativada.", "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  }
  const tile = (k, v, s) => el("div", { class: "tile" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v), s ? el("div", { class: "s" }, s) : null);
  function paintClock() {
    if (!last?.now_playing) { bar.style.width = "0"; elapsed.textContent = ""; remain.textContent = ""; return; }
    const np = last.now_playing; const el_ms = Date.now() + S.tzOffset - new Date(np.started_at).getTime();
    bar.style.width = `${Math.min(100, (el_ms / np.duration_ms) * 100)}%`;
    elapsed.textContent = fmtDur(el_ms); remain.textContent = "−" + fmtDur(np.duration_ms - el_ms);
  }
  async function load() {
    const s = await api(`/api/stations/${sid}/status`); last = s; setOnAir(s);
    S.tzOffset = new Date(s.server_time).getTime() - Date.now();
    const np = s.now_playing;
    title.textContent = np ? np.title : (s.status === "dead_air" ? "Sem conteúdo disponível (silêncio)" : "Nada no ar");
    artist.textContent = np?.artist || "";
    srcB.textContent = np ? ({ manual: "Fila editorial", rotation: "Rotação de contingência", fallback: "Mídia de contingência" }[np.source] || np.source) : s.status;
    eq.className = "eq" + (s.status === "on_air" ? "" : " paused");
    cover.replaceChildren(el("span", { html: ICON.logo }));
    if (np?.media_id && can("library.view")) {
      api(`/api/stations/${sid}/media/${np.media_id}`).then((m) => { if (m.has_cover) imgAuth(`/api/stations/${sid}/media/${np.media_id}/cover`).then((u) => u && cover.replaceChildren(el("img", { src: u, alt: "" }))); }).catch(() => {});
    }
    nextEl.textContent = s.next ? `${s.next.title}${s.next.artist ? " — " + s.next.artist : ""}` : "—";
    btnStart.disabled = s.stream_enabled; btnStop.disabled = !s.stream_enabled;
    const l = s.listeners;
    tiles.replaceChildren(
      tile("Ouvintes agora", l.current ?? el("span", { class: "na" }, "sem dado"), "conexões no Icecast"),
      tile("Pico hoje", l.peak_today ?? el("span", { class: "na" }, "—"), "amostras reais"),
      tile("Execuções hoje", s.plays_today, "registradas pelo playout"),
      tile("Biblioteca", fmtBytes(s.storage_bytes), s.disk ? `${fmtBytes(s.disk.free)} livres no disco` : ""),
      tile("Carga do host", s.host_load != null ? `${Math.round(s.host_load * 100)}%` : el("span", { class: "na" }, "—"), "servidor de playout"),
      tile("Receitas", el("span", { class: "na" }, "Indisponível"), "módulo comercial — Fase 3"));
    alerts.replaceChildren(...(s.alerts.length ? s.alerts.map((a) => el("div", { class: `alert ${a.level}` }, a.text)) : [el("div", { class: "alert" }, "Nenhum alerta.")]));
    q.replaceChildren(...(s.queue.length ? s.queue.slice(0, 6).map((it, i) => el("li", {}, el("span", { class: "idx" }, i + 1), el("div", { class: "t" }, el("b", {}, it.title), el("span", { class: "sub" }, it.artist || "")), el("span", { class: "num" }, fmtDur(it.effective_ms)))) :
      [el("li", {}, el("span", { class: "sub" }, s.stream_enabled ? "Fila vazia — a rotação de contingência assume, se ativa." : "Fila vazia."))]));
    const dot = (ok, warn) => el("span", { class: `dot ${ok ? "ok" : warn ? "warn" : "bad"}` });
    health.replaceChildren(el("table", {}, el("tbody", {},
      el("tr", {}, el("td", {}, dot(s.service_online), " Serviço de playout"), el("td", {}, s.service_online ? `ativo (heartbeat há ${s.heartbeat_age_s}s)` : "offline")),
      el("tr", {}, el("td", {}, dot(s.encoder_connected, !s.stream_enabled), " Servidor de streaming"), el("td", {}, s.encoder_connected ? `conectado desde ${fmtTime(s.encoder_since)}` : (s.stream_enabled ? (s.last_error || "desconectado") : "transmissão desativada"))),
      el("tr", {}, el("td", {}, "Formato"), el("td", { class: "num" }, `${s.format.codec.toUpperCase()} ${s.format.bitrate_kbps} kbps · ${s.format.sample_rate / 1000} kHz · ${s.format.channels === 2 ? "estéreo" : "mono"} · ${s.format.mount}`)),
      el("tr", {}, el("td", {}, "Reconexões"), el("td", { class: "num" }, s.reconnects)),
      el("tr", {}, el("td", {}, "Buffer do encoder"), el("td", { class: "num" }, s.buffer_ms != null ? `${s.buffer_ms} ms` : "—")),
      el("tr", {}, el("td", {}, "URL pública"), el("td", {}, s.public_listen_url ? el("code", {}, s.public_listen_url) : el("span", { class: "na" }, "não configurada"))))));
    document.getElementById("hb").className = `dot ${s.service_online ? "ok" : "bad"}`;
    document.getElementById("hbt").textContent = s.service_online ? "playout ativo" : "playout offline";
    paintClock();
  }
  await load();
  S.timers.push(setInterval(() => load().catch(() => {}), 3000), setInterval(paintClock, 500));
}
const _imgCache = new Map();
async function imgAuth(url) {
  if (_imgCache.has(url)) return _imgCache.get(url);
  try { const r = await api(url, { raw: true }); if (!r.ok) return null; const u = URL.createObjectURL(await r.blob()); _imgCache.set(url, u); return u; } catch { return null; }
}

// ------------------------------------------------------------------ biblioteca
let previewAudio = null;
async function viewLibrary(c) {
  const sid = S.sid;
  const st = { q: "", kind: "", license_status: "", sort: "recent", offset: 0 };
  const tbody = el("tbody"), info = el("span", { class: "sub" }), pager = el("div", { class: "controls" });
  const search = el("input", { type: "search", placeholder: "Buscar título, artista, álbum, gênero, tag…", oninput: debounce((e) => { st.q = e.target.value; st.offset = 0; load(); }, 300) });
  let meta = { kinds: {}, license_statuses: {} };
  const kindSel = el("select", { onchange: (e) => { st.kind = e.target.value; st.offset = 0; load(); } });
  const licSel = el("select", { onchange: (e) => { st.license_status = e.target.value; st.offset = 0; load(); } });
  const sortSel = el("select", { onchange: (e) => { st.sort = e.target.value; load(); } },
    [["recent", "Mais recentes"], ["title", "Título"], ["artist", "Artista"], ["duration", "Duração"]].map(([v, l]) => el("option", { value: v }, l)));
  const uploads = el("div", { class: "uploads" });
  const fileIn = el("input", { type: "file", multiple: true, accept: ".mp3,.wav,.flac,.ogg,.oga,.opus,.m4a,.mp4,.aac,.aif,.aiff", class: "sr-only", onchange: (e) => queueUploads([...e.target.files]) });
  const uploadKind = el("select", { "aria-label": "Tipo do envio" });
  const drop = el("div", { class: "drop", tabindex: 0, role: "button", onclick: () => fileIn.click(), onkeydown: (e) => (e.key === "Enter" || e.key === " ") && fileIn.click() },
    el("b", {}, "Arraste arquivos de áudio aqui"), " ou clique para selecionar", el("div", { class: "hint" }, "MP3, WAV, FLAC, OGG/Opus, M4A/AAC, AIFF — validados no servidor (FFprobe). Loudness, silêncio e capa são analisados em segundo plano."));
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("hover"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("hover"); }));
  drop.addEventListener("drop", (e) => queueUploads([...e.dataTransfer.files]));

  c.append(pageH("Biblioteca musical e sonora", info),
    can("library.upload") ? el("div", { class: "card", style: "margin-bottom:16px" }, el("div", { class: "filters" }, el("label", { style: "margin:auto 0" }, "Enviar como:"), uploadKind), drop, fileIn, uploads) : null,
    el("div", { class: "card" }, el("div", { class: "filters" }, search, kindSel, licSel, sortSel),
      el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["", "Título / artista", "Tipo", "Duração", "Loudness", "Licença", "Análise", ""].map((h) => el("th", {}, h)))), tbody)), pager));

  async function load() {
    const p = new URLSearchParams({ ...st, limit: 50 });
    const d = await api(`/api/stations/${sid}/media?${p}`);
    meta = d;
    if (!kindSel.options.length) {
      kindSel.append(el("option", { value: "" }, "Todos os tipos"), ...Object.entries(d.kinds).map(([k, v]) => el("option", { value: k }, v)));
      licSel.append(el("option", { value: "" }, "Todas as licenças"), ...Object.entries(d.license_statuses).map(([k, v]) => el("option", { value: k }, v)));
      uploadKind.append(...Object.entries(d.kinds).map(([k, v]) => el("option", { value: k }, v)));
    }
    info.textContent = `${d.total} itens · ${fmtBytes(d.storage_bytes)}`;
    tbody.replaceChildren(...(d.items.length ? d.items.map(row) : [el("tr", {}, el("td", { colspan: 8, class: "sub" }, "Nenhuma mídia encontrada."))]));
    pager.replaceChildren(
      el("button", { class: "btn sm", disabled: st.offset === 0, onclick: () => { st.offset = Math.max(0, st.offset - 50); load(); } }, "← Anterior"),
      el("span", { class: "sub", style: "margin:auto 0" }, `${d.total ? st.offset + 1 : 0}–${Math.min(st.offset + 50, d.total)} de ${d.total}`),
      el("button", { class: "btn sm", disabled: st.offset + 50 >= d.total, onclick: () => { st.offset += 50; load(); } }, "Próxima →"));
  }
  function licBadge(m) {
    const cls = m.license_status === "blocked" ? "bad" : m.license_status === "unknown" ? "warn" : "ok";
    return el("span", { class: `badge ${cls}`, title: m.unplayable_reason || "" }, meta.license_statuses[m.license_status] || m.license_status);
  }
  function row(m) {
    const play = el("button", { class: "btn icon sm", title: "Pré-escuta", "aria-label": `Pré-escuta de ${m.title}`, onclick: () => preview(m, play) }, "▶");
    return el("tr", {},
      el("td", {}, play),
      el("td", {}, el("b", {}, m.title), m.is_demo ? el("span", { class: "badge info", style: "margin-left:6px" }, "DEMO") : null, el("div", { class: "sub" }, [m.artist, m.album].filter(Boolean).join(" · ") || "—")),
      el("td", {}, meta.kinds[m.kind] || m.kind),
      el("td", { class: "num" }, fmtDur(m.effective_ms), m.effective_ms !== m.duration_ms ? el("div", { class: "sub" }, `arquivo ${fmtDur(m.duration_ms)}`) : null),
      el("td", { class: "num" }, m.loudness_lufs != null ? `${m.loudness_lufs.toFixed(1)} LUFS` : "—", m.true_peak_db != null ? el("div", { class: "sub" }, `TP ${m.true_peak_db.toFixed(1)} dB`) : null),
      el("td", {}, licBadge(m)),
      el("td", {}, el("span", { class: `badge ${m.analysis_status === "done" ? "ok" : m.analysis_status === "failed" ? "bad" : "warn"}`, title: m.analysis_error || "" }, { done: "concluída", failed: "falhou", pending: "na fila" }[m.analysis_status])),
      el("td", {}, el("div", { class: "row-actions" },
        can("queue.edit") ? el("button", { class: "btn sm", disabled: !m.playable, title: m.unplayable_reason || "Adicionar ao fim da fila", onclick: () => addQueue(m, "end") }, "+ Fila") : null,
        can("queue.edit") ? el("button", { class: "btn sm", disabled: !m.playable, title: "Tocar em seguida", onclick: () => addQueue(m, "next") }, "Próxima") : null,
        can("library.edit") ? el("button", { class: "btn sm", onclick: () => editMedia(m.id) }, "Editar") : null)));
  }
  async function preview(m, btn) {
    if (previewAudio && previewAudio.dataset.id === m.id && !previewAudio.paused) { previewAudio.pause(); btn.textContent = "▶"; return; }
    const { url } = await api(`/api/stations/${sid}/media/${m.id}/preview-url`);
    if (previewAudio) previewAudio.pause();
    document.querySelectorAll("td .btn.icon").forEach((b) => (b.textContent = "▶"));
    previewAudio = new Audio(url); previewAudio.dataset.id = m.id; previewAudio.currentTime = (m.cue_in_ms || 0) / 1000;
    previewAudio.play().then(() => (btn.textContent = "❚❚")).catch((e) => toast("Não foi possível reproduzir: " + e.message, "err"));
    previewAudio.onended = () => (btn.textContent = "▶");
  }
  async function addQueue(m, where) {
    try { await api(`/api/stations/${sid}/queue`, { method: "POST", body: { media_id: m.id, where } }); toast(`“${m.title}” ${where === "next" ? "toca em seguida" : "adicionada à fila"}.`, "ok"); }
    catch (e) { toast(e.message, "err"); }
  }
  async function editMedia(id) {
    const m = await api(`/api/stations/${sid}/media/${id}`);
    const f = {};
    const inp = (k, label, attrs = {}, full = false) => { f[k] = el("input", { id: `f-${k}`, value: m[k] ?? "", ...attrs }); return el("div", { class: "field" + (full ? " full" : "") }, el("label", { for: `f-${k}` }, label), f[k]); };
    const sel = (k, label, opts, disabled) => { f[k] = el("select", { id: `f-${k}`, disabled }, Object.entries(opts).map(([v, l]) => el("option", { value: v, selected: m[k] === v }, l))); return el("div", { class: "field" }, el("label", { for: `f-${k}` }, label), f[k]); };
    const canLic = can("library.license");
    const body = el("div", {},
      el("div", { class: "form-grid" },
        inp("title", "Título", {}, true), inp("artist", "Artista"), inp("album", "Álbum"), inp("genre", "Gênero"),
        inp("year", "Ano", { type: "number", min: 1800, max: 2200 }), sel("kind", "Tipo", meta.kinds),
        inp("bpm", "BPM", { type: "number", step: "0.1", min: 20, max: 300 }), inp("rating", "Classificação editorial (1–5)", { type: "number", min: 1, max: 5 }),
        el("div", { class: "field full" }, el("label", { for: "f-tags" }, "Tags (separadas por vírgula)"), (f.tags = el("input", { id: "f-tags", value: (m.tags || []).join(", ") }))),
        el("div", { class: "full nav-h", style: "margin-left:0" }, "Pontos de entrada/saída e fades (não destrutivo)"),
        inp("cue_in_ms", "Entrada (ms)", { type: "number", min: 0, max: m.duration_ms }), inp("cue_out_ms", "Saída (ms) — vazio = fim do arquivo", { type: "number", min: 0, max: m.duration_ms }),
        inp("fade_in_ms", "Fade in (ms)", { type: "number", min: 0 }), inp("fade_out_ms", "Fade out (ms)", { type: "number", min: 0 }),
        el("div", { class: "hint full" }, `Arquivo: ${fmtDur(m.duration_ms)} · ${m.codec} ${m.bitrate_kbps || "?"} kbps · ${m.sample_rate || "?"} Hz · silêncio inicial ${m.lead_silence_ms ?? "—"} ms, final ${m.trail_silence_ms ?? "—"} ms`),
        el("div", { class: "full nav-h", style: "margin-left:0" }, "Direitos e licença" + (canLic ? "" : " (somente proprietário/diretor)")),
        sel("license_status", "Situação", meta.license_statuses, !canLic), inp("license_expires_at", "Vigência até", { type: "date", disabled: !canLic }),
        inp("rights_holder", "Titular dos direitos", { disabled: !canLic }), inp("license_ref", "Referência da licença/contrato", { disabled: !canLic }),
        inp("isrc", "ISRC", { maxlength: 15 })),
      el("p", { class: "hint" }, `Executada ${m.play_count} vez(es).`, m.recent_plays.length ? ` Última: ${fmtDate(m.recent_plays[0].started_at)}.` : ""));
    const num = (v) => (v === "" ? null : Number(v));
    const res = await modal(`Editar — ${m.title}`, body, [
      { label: "Excluir", class: "danger", handler: async () => {
        if (!(await confirmBox("Excluir mídia", "Mídias que já passaram pela fila são arquivadas (o histórico de execução é preservado). Continuar?", true))) return false;
        const r = await api(`/api/stations/${sid}/media/${id}`, { method: "DELETE" }); toast(r.archived ? "Mídia arquivada." : "Mídia excluída.", "ok"); return "changed";
      } },
      { label: "Reanalisar", handler: async () => { await api(`/api/stations/${sid}/media/${id}/reanalyze`, { method: "POST" }); toast("Análise reenfileirada.", "ok"); return "changed"; } },
      { label: "Cancelar", value: null },
      { label: "Salvar", class: "primary", handler: async () => {
        const b = { title: f.title.value, artist: f.artist.value, album: f.album.value, genre: f.genre.value, kind: f.kind.value, isrc: f.isrc.value,
          year: num(f.year.value), bpm: num(f.bpm.value), rating: num(f.rating.value), tags: f.tags.value.split(",").map((t) => t.trim()).filter(Boolean),
          cue_in_ms: num(f.cue_in_ms.value) ?? 0, cue_out_ms: num(f.cue_out_ms.value), fade_in_ms: num(f.fade_in_ms.value) ?? 0, fade_out_ms: num(f.fade_out_ms.value) ?? 0 };
        if (canLic) Object.assign(b, { license_status: f.license_status.value, license_expires_at: f.license_expires_at.value, rights_holder: f.rights_holder.value, license_ref: f.license_ref.value });
        await api(`/api/stations/${sid}/media/${id}`, { method: "PATCH", body: b }); toast("Mídia atualizada.", "ok"); return "changed";
      } }]);
    if (res === "changed") load();
  }
  async function queueUploads(files) {
    for (const file of files) {
      const bar = el("i"), status = el("span", { class: "sub" }, "enviando…");
      uploads.prepend(el("div", { class: "up" }, el("span", {}, file.name), el("div", { class: "bar" }, bar), status));
      try {
        await uploadOne(file, (p) => (bar.style.width = `${p}%`));
        status.textContent = "✓ enviado"; status.style.color = "var(--green)";
      } catch (e) { status.textContent = "✕ " + e.message; status.style.color = "var(--red)"; bar.style.background = "var(--red)"; }
    }
    load();
  }
  function uploadOne(file, onProgress) {
    return new Promise((resolve, reject) => {
      const fd = new FormData(); fd.append("file", file); fd.append("kind", uploadKind.value || "music");
      const x = new XMLHttpRequest();
      x.open("POST", `/api/stations/${sid}/media`); x.setRequestHeader("Authorization", `Bearer ${S.token}`);
      x.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
      x.onload = () => { let d = {}; try { d = JSON.parse(x.responseText); } catch { /* vazio */ } x.status === 200 ? resolve(d) : reject(new Error(d.error || `erro ${x.status}`)); };
      x.onerror = () => reject(new Error("falha de rede"));
      x.send(fd);
    });
  }
  await load();
  S.timers.push(setInterval(() => { if (tbody.querySelector(".badge.warn")) load().catch(() => {}); }, 8000));
}
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

// ------------------------------------------------------------------ fila
async function viewQueue(c) {
  const sid = S.sid;
  const list = el("ol", { class: "qlist", "aria-label": "Fila de execução" }), total = el("span", { class: "sub" });
  const editable = can("queue.edit");
  c.append(pageH("Fila de execução", total, editable ? el("button", { class: "btn danger sm", onclick: async () => {
    if (await confirmBox("Limpar fila", "Remover todos os itens pendentes?", true)) { await api(`/api/stations/${sid}/queue`, { method: "DELETE" }); load(); }
  } }, "Limpar fila") : null),
    el("p", { class: "hint" }, "A fila editorial tem prioridade sobre a rotação de contingência. Itens persistem no banco e sobrevivem a reinícios do playout. Arraste para reordenar ou use as setas."),
    el("div", { class: "card" }, list));
  let items = [];
  async function load() {
    items = (await api(`/api/stations/${sid}/queue`)).items;
    total.textContent = `${items.length} itens · ${fmtDur(items.reduce((a, b) => a + b.effective_ms, 0))}`;
    list.replaceChildren(...(items.length ? items.map(li) : [el("li", {}, el("span", { class: "sub" }, "Fila vazia. Adicione mídias pela Biblioteca."))]));
  }
  const move = async (id, index) => { await api(`/api/stations/${sid}/queue/${id}/move`, { method: "POST", body: { index } }); load(); };
  let dragId = null;
  function li(it, i) {
    const n = el("li", { draggable: editable ? "true" : null, "data-id": it.id },
      editable ? el("span", { class: "grip", "aria-hidden": "true" }, "⋮⋮") : null, el("span", { class: "idx" }, i + 1),
      el("div", { class: "t" }, el("b", {}, it.title), el("span", { class: "sub" }, it.artist || "—")),
      !it.playable ? el("span", { class: "badge bad", title: it.unplayable_reason }, "indisponível") : null,
      el("span", { class: "num" }, fmtDur(it.effective_ms)),
      editable ? el("div", { class: "row-actions" },
        el("button", { class: "btn icon sm", "aria-label": "Subir", disabled: i === 0, onclick: () => move(it.id, i - 1) }, "↑"),
        el("button", { class: "btn icon sm", "aria-label": "Descer", disabled: i === items.length - 1, onclick: () => move(it.id, i + 1) }, "↓"),
        el("button", { class: "btn icon sm", "aria-label": "Remover", onclick: async () => { await api(`/api/stations/${sid}/queue/${it.id}`, { method: "DELETE" }); load(); } }, "✕")) : null);
    if (editable) {
      n.addEventListener("dragstart", () => { dragId = it.id; n.classList.add("dragging"); });
      n.addEventListener("dragend", () => n.classList.remove("dragging"));
      n.addEventListener("dragover", (e) => { e.preventDefault(); n.classList.add("over"); });
      n.addEventListener("dragleave", () => n.classList.remove("over"));
      n.addEventListener("drop", (e) => { e.preventDefault(); n.classList.remove("over"); if (dragId && dragId !== it.id) move(dragId, i); });
    }
    return n;
  }
  await load();
  S.timers.push(setInterval(() => { if (!document.querySelector(".dragging")) load().catch(() => {}); }, 4000));
}

// ------------------------------------------------------------------ transmissão
async function viewStream(c) {
  const sid = S.sid;
  const d = await api(`/api/stations/${sid}/stream`);
  const ro = !can("stream.edit");
  const f = {};
  const inp = (k, label, attrs = {}, hint) => { f[k] = el("input", { id: `s-${k}`, value: d[k] ?? "", disabled: ro, ...attrs }); return el("div", { class: "field" }, el("label", { for: `s-${k}` }, label), f[k], hint ? el("div", { class: "hint" }, hint) : null); };
  const sel = (k, label, opts) => { f[k] = el("select", { id: `s-${k}`, disabled: ro }, opts.map(([v, l]) => el("option", { value: v, selected: String(d[k]) === String(v) }, l))); return el("div", { class: "field" }, el("label", { for: `s-${k}` }, label), f[k]); };
  const chk = (k, label) => { f[k] = el("input", { type: "checkbox", id: `s-${k}`, checked: d[k], disabled: ro, style: "width:auto" }); return el("div", { class: "field" }, el("label", { for: `s-${k}`, style: "display:flex;gap:8px;align-items:center;color:var(--text)" }, f[k], label)); };
  const media = can("library.view") ? (await api(`/api/stations/${sid}/media?limit=200&sort=title`)).items : [];
  f.fallback_media_id = el("select", { id: "s-fb", disabled: ro }, el("option", { value: "" }, "— nenhuma —"), media.filter((m) => m.playable).map((m) => el("option", { value: m.id, selected: m.id === d.fallback_media_id }, m.title)));
  c.append(pageH("Transmissão digital", el("span", { class: `badge ${d.enabled ? "ok" : ""}` }, d.enabled ? "ativada" : "desativada")),
    el("div", { class: "grid g-2" },
      el("div", { class: "card" }, el("h3", {}, "Servidor de ingestão (Icecast 2.4+)"),
        el("div", { class: "form-grid" }, inp("host", "Host"), inp("port", "Porta", { type: "number" }), inp("mount", "Mountpoint", {}, "Ex.: /minha-radio"),
          inp("source_user", "Usuário de source"),
          el("div", { class: "field full" }, el("label", { for: "s-pw" }, "Senha de source"), (f.source_password = el("input", { id: "s-pw", type: "password", autocomplete: "new-password", disabled: ro, placeholder: d.has_source_password ? "•••••••• (configurada — deixe em branco para manter)" : "não configurada" })),
            el("div", { class: "hint" }, "Fica cifrada no servidor e nunca é enviada ao navegador nem à página pública.")),
          chk("use_tls", "Conectar com TLS (Icecast com HTTPS)")),
        el("h3", { style: "margin-top:12px" }, "Distribuição pública"),
        inp("public_listen_url", "URL pública de escuta", { type: "url", placeholder: "https://stream.suaradio.com.br/minha-radio" }, "É a URL usada pelo player público. Em sites HTTPS ela também precisa ser HTTPS (proxy reverso ou CDN)."),
        inp("status_url", "URL de status (opcional)", { type: "url", placeholder: "http://127.0.0.1:8000/status-json.xsl" }, "Usada para contar ouvintes. Padrão: status-json.xsl do host acima.")),
      el("div", { class: "card" }, el("h3", {}, "Codificação"),
        el("div", { class: "form-grid" },
          sel("format", "Formato", [["mp3", "MP3 (máxima compatibilidade)"], ["aac", "AAC (ADTS)"], ["opus", "Opus em Ogg (eficiente; sem metadados ICY)"]]),
          sel("bitrate_kbps", "Bitrate", d.bitrates.map((b) => [b, `${b} kbps`])),
          sel("sample_rate", "Sample rate", [[44100, "44,1 kHz"], [48000, "48 kHz"]]), sel("channels", "Canais", [[2, "Estéreo"], [1, "Mono"]]),
          inp("stream_name", "Nome do stream"), inp("stream_genre", "Gênero")),
        el("h3", { style: "margin-top:12px" }, "Áudio e contingência"),
        el("div", { class: "form-grid" }, chk("normalize", "Normalizar loudness no ar"), inp("target_lufs", "Alvo (LUFS)", { type: "number", step: "0.5", min: -30, max: -8 }),
          chk("rotation_fallback", "Rotação de contingência quando a fila esvaziar"), inp("no_repeat_window", "Não repetir as últimas N execuções", { type: "number", min: 0, max: 500 }),
          el("div", { class: "field full" }, el("label", { for: "s-fb" }, "Mídia de contingência (último recurso)"), f.fallback_media_id)))),
    ro ? el("p", { class: "hint" }, "Somente leitura para o seu papel.") : el("div", { class: "controls" }, el("button", { class: "btn primary", onclick: save }, "Salvar configuração"),
      el("span", { class: "hint", style: "margin:auto 0" }, "Ao salvar, o serviço de playout recarrega a configuração e reconecta sozinho.")));
  async function save() {
    const b = { host: f.host.value, port: Number(f.port.value), mount: f.mount.value, source_user: f.source_user.value, use_tls: f.use_tls.checked,
      public_listen_url: f.public_listen_url.value, status_url: f.status_url.value, format: f.format.value, bitrate_kbps: Number(f.bitrate_kbps.value),
      sample_rate: Number(f.sample_rate.value), channels: Number(f.channels.value), stream_name: f.stream_name.value, stream_genre: f.stream_genre.value,
      normalize: f.normalize.checked, target_lufs: Number(f.target_lufs.value), rotation_fallback: f.rotation_fallback.checked,
      no_repeat_window: Number(f.no_repeat_window.value), fallback_media_id: f.fallback_media_id.value || null };
    if (f.source_password.value) b.source_password = f.source_password.value;
    try { await api(`/api/stations/${sid}/stream`, { method: "PUT", body: b }); toast("Configuração salva.", "ok"); render(); } catch (e) { toast(e.message, "err"); }
  }
}

// ------------------------------------------------------------------ relatórios
async function viewReports(c) {
  const sid = S.sid;
  const today = new Date(); const ymd = (d) => d.toISOString().slice(0, 10);
  const from = el("input", { type: "date", value: ymd(new Date(today - 86400000)) }), to = el("input", { type: "date", value: ymd(today) });
  const hours = el("select", {}, [[24, "24 horas"], [72, "3 dias"], [168, "7 dias"]].map(([v, l]) => el("option", { value: v }, l)));
  const sum = el("div", { class: "tiles" }), tbody = el("tbody"), chart = el("div", { class: "chart" }), chartInfo = el("p", { class: "hint" });
  const range = () => ({ from: new Date(from.value + "T00:00:00").toISOString(), to: new Date(to.value + "T23:59:59.999").toISOString() });
  c.append(pageH("Relatórios"),
    el("div", { class: "card", style: "margin-bottom:16px" }, el("h3", {}, "Conexões simultâneas (Icecast)"), el("div", { class: "filters" }, hours), chart, chartInfo),
    el("div", { class: "card" }, el("h3", {}, "Execuções registradas pelo playout"),
      el("div", { class: "filters" }, el("label", { style: "margin:auto 0" }, "De"), from, el("label", { style: "margin:auto 0" }, "até"), to,
        el("button", { class: "btn", onclick: loadPlays }, "Filtrar"), el("button", { class: "btn primary", onclick: exportCsv }, "Exportar CSV")),
      sum, el("div", { class: "table-wrap", style: "margin-top:12px" }, el("table", {}, el("thead", {}, el("tr", {}, ["Início", "Título", "Artista", "Origem", "Tocado", "Encerramento"].map((h) => el("th", {}, h)))), tbody))));
  hours.onchange = loadListeners;
  async function loadPlays() {
    const d = await api(`/api/stations/${sid}/plays?${new URLSearchParams(range())}`);
    const t = (k, v) => el("div", { class: "tile" }, el("div", { class: "k" }, k), el("div", { class: "v" }, v));
    sum.replaceChildren(t("Execuções", d.summary.total), t("Concluídas", d.summary.completed), t("Puladas", d.summary.skipped), t("Interrompidas", d.summary.interrupted), t("Tempo no ar", fmtDur(d.summary.played_ms)));
    const reason = { completed: ["ok", "concluída"], skipped: ["warn", "pulada"], interrupted: ["bad", "interrompida"], error: ["bad", "erro"], stopped: ["warn", "parada"] };
    tbody.replaceChildren(...(d.items.length ? d.items.map((p) => el("tr", {}, el("td", { class: "num" }, fmtDate(p.started_at)), el("td", {}, p.title), el("td", {}, p.artist || "—"),
      el("td", {}, { manual: "Fila", rotation: "Rotação", fallback: "Contingência" }[p.source] || p.source), el("td", { class: "num" }, fmtDur(p.played_ms)),
      el("td", {}, p.end_reason ? el("span", { class: `badge ${reason[p.end_reason]?.[0] || ""}` }, reason[p.end_reason]?.[1] || p.end_reason) : el("span", { class: "badge info" }, "no ar")))) :
      [el("tr", {}, el("td", { colspan: 6, class: "sub" }, "Sem execuções no período."))]));
  }
  async function exportCsv() {
    const r = await api(`/api/stations/${sid}/plays.csv?${new URLSearchParams(range())}`, { raw: true });
    const blob = await r.blob(); const a = el("a", { href: URL.createObjectURL(blob), download: `execucoes-${station().slug}-${from.value}-a-${to.value}.csv` });
    document.body.append(a); a.click(); a.remove();
  }
  async function loadListeners() {
    const d = await api(`/api/stations/${sid}/listeners?hours=${hours.value}`);
    chartInfo.textContent = d.samples ? `Pico ${d.peak} · média ${d.avg} · ${d.samples} amostras. ${d.note}` : `Sem amostras no período. ${d.note}`;
    chart.replaceChildren(lineChart(d.points.map((p) => [new Date(p.taken_at).getTime(), p.listeners])));
  }
  await Promise.all([loadPlays(), loadListeners()]);
}
function lineChart(pts) {
  const W = 800, H = 200, P = 28;
  if (!pts.length) return el("div", { class: "na", style: "padding:40px;text-align:center" }, "Sem dados reais para exibir.");
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1, y1 = Math.max(1, ...ys);
  const X = (x) => P + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * P), Y = (y) => H - P - (y / y1) * (H - 2 * P);
  const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
  const area = `${d} L${X(x1)},${H - P} L${X(x0)},${H - P} Z`;
  const svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Conexões simultâneas ao longo do tempo">
    <defs><linearGradient id="ga" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#19d3e6" stop-opacity=".35"/><stop offset="1" stop-color="#19d3e6" stop-opacity="0"/></linearGradient></defs>
    <line x1="${P}" x2="${W - P}" y1="${H - P}" y2="${H - P}" stroke="currentColor" opacity=".2"/>
    <text x="${P}" y="${P - 8}" fill="currentColor" opacity=".6" font-size="11">${y1}</text>
    <path d="${area}" fill="url(#ga)"/><path d="${d}" fill="none" stroke="#19d3e6" stroke-width="2"/></svg>`;
  return el("div", { html: svg, style: "color:var(--muted)" });
}

// ------------------------------------------------------------------ equipe, auditoria, emissora
async function viewTeam(c) {
  const sid = S.sid;
  const d = await api(`/api/stations/${sid}/members`);
  const manage = can("members.manage");
  const email = el("input", { type: "email", placeholder: "email@exemplo.com", id: "m-email" });
  const role = el("select", { id: "m-role" }, d.roles.map((r) => el("option", { value: r.id }, r.label)));
  c.append(pageH("Equipe da emissora"),
    manage ? el("div", { class: "card", style: "margin-bottom:16px" }, el("h3", {}, "Adicionar ou alterar membro"),
      el("div", { class: "filters" }, email, role, el("button", { class: "btn primary", onclick: async () => {
        try {
          const r = await api(`/api/stations/${sid}/members`, { method: "POST", body: { email: email.value, role: role.value } });
          if (r.temporary_password) await modal("Usuário criado", el("div", {}, el("p", {}, "Envie esta senha temporária por um canal seguro. Ela não será exibida novamente."), el("div", { class: "secret mono" }, r.temporary_password)), [{ label: "Fechar", class: "primary" }]);
          else toast("Membro atualizado.", "ok");
          render();
        } catch (e) { toast(e.message, "err"); }
      } }, "Salvar")), el("p", { class: "hint" }, "Se o e-mail não existir, um usuário é criado com senha temporária.")) : null,
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, ["Nome", "E-mail", "Papel", ""].map((h) => el("th", {}, h)))),
      el("tbody", {}, d.members.map((m) => el("tr", {}, el("td", {}, m.name), el("td", {}, m.email), el("td", {}, el("span", { class: "badge info" }, m.role_label)),
        el("td", {}, manage ? el("button", { class: "btn sm danger", onclick: async () => {
          if (!(await confirmBox("Remover membro", `Remover ${m.email} desta emissora?`, true))) return;
          try { await api(`/api/stations/${sid}/members/${m.id}`, { method: "DELETE" }); render(); } catch (e) { toast(e.message, "err"); }
        } }, "Remover") : null)))))));
}
async function viewAudit(c) {
  const d = await api(`/api/stations/${S.sid}/audit`);
  c.append(pageH("Auditoria"), el("div", { class: "card table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Quando", "Usuário", "Ação", "Detalhes", "IP"].map((h) => el("th", {}, h)))),
    el("tbody", {}, d.items.map((a) => el("tr", {}, el("td", { class: "num" }, fmtDate(a.created_at)), el("td", {}, a.email || "—"), el("td", {}, el("code", {}, a.action)), el("td", { class: "sub" }, a.details || ""), el("td", { class: "num" }, a.ip || "")))))));
}
async function viewStation(c) {
  const s = await api(`/api/stations/${S.sid}`);
  const f = {};
  const inp = (k, label, attrs = {}) => { f[k] = el("input", { id: `st-${k}`, value: s[k] ?? "", ...attrs }); return el("div", { class: "field" }, el("label", { for: `st-${k}` }, label), f[k]); };
  f.show_history = el("input", { type: "checkbox", checked: s.show_history, style: "width:auto", id: "st-h" });
  c.append(pageH("Emissora", el("a", { class: "btn ghost", href: `/r/${s.slug}`, target: "_blank", rel: "noopener" }, "Player público ↗")),
    el("div", { class: "card" }, el("div", { class: "form-grid" }, inp("name", "Nome"), inp("slogan", "Slogan"), inp("description", "Descrição"), inp("timezone", "Fuso horário (IANA)"),
      inp("primary_color", "Cor principal", { type: "color" }),
      el("div", { class: "field" }, el("label", { for: "st-h", style: "display:flex;gap:8px;align-items:center;color:var(--text)" }, f.show_history, "Exibir histórico de faixas no player público"))),
      el("p", { class: "hint" }, `Endereço: /r/${s.slug} · plano: ${s.plan} (planos comerciais na Fase 4)`),
      el("button", { class: "btn primary", onclick: async () => {
        try { await api(`/api/stations/${S.sid}`, { method: "PATCH", body: { name: f.name.value, slogan: f.slogan.value, description: f.description.value, timezone: f.timezone.value, primary_color: f.primary_color.value, show_history: f.show_history.checked } }); toast("Emissora atualizada.", "ok"); boot(); }
        catch (e) { toast(e.message, "err"); }
      } }, "Salvar")));
}

// ------------------------------------------------------------------ conta
async function viewAccount(c) {
  const cur = el("input", { type: "password", autocomplete: "current-password", id: "pw-cur" }), nw = el("input", { type: "password", autocomplete: "new-password", id: "pw-new" });
  const mfaBox = el("div");
  c.append(pageH("Minha conta"), el("div", { class: "grid g-2" },
    el("div", { class: "card" }, el("h3", {}, "Senha"), el("div", { class: "field" }, el("label", { for: "pw-cur" }, "Senha atual"), cur),
      el("div", { class: "field" }, el("label", { for: "pw-new" }, "Nova senha"), nw, el("div", { class: "hint" }, "Mínimo de 10 caracteres, maiúsculas, minúsculas e número. Todas as outras sessões serão encerradas.")),
      el("button", { class: "btn primary", onclick: async () => { try { await api("/api/me/password", { method: "POST", body: { current_password: cur.value, new_password: nw.value } }); toast("Senha alterada.", "ok"); cur.value = nw.value = ""; } catch (e) { toast(e.message, "err"); } } }, "Alterar senha")),
    el("div", { class: "card" }, el("h3", {}, "Verificação em duas etapas (TOTP)"), mfaBox)));
  const paint = () => {
    if (S.user.mfa_enabled) {
      const pw = el("input", { type: "password", placeholder: "Senha" }), code = el("input", { placeholder: "Código atual", inputmode: "numeric" });
      mfaBox.replaceChildren(el("p", {}, el("span", { class: "badge ok" }, "ativa")), el("div", { class: "filters" }, pw, code,
        el("button", { class: "btn danger", onclick: async () => { try { await api("/api/me/mfa/disable", { method: "POST", body: { password: pw.value, code: code.value } }); S.user.mfa_enabled = false; paint(); toast("MFA desativada.", "ok"); } catch (e) { toast(e.message, "err"); } } }, "Desativar")));
    } else {
      mfaBox.replaceChildren(el("p", { class: "sub" }, "Proteja sua conta com um aplicativo autenticador (Google Authenticator, Microsoft Authenticator, 1Password…)."),
        el("button", { class: "btn primary", onclick: async () => {
          const d = await api("/api/me/mfa/setup", { method: "POST" });
          const code = el("input", { placeholder: "Código de 6 dígitos", inputmode: "numeric", maxlength: 6 });
          mfaBox.replaceChildren(el("p", {}, "No aplicativo, adicione uma conta pela opção de chave manual e informe:"), el("div", { class: "secret mono" }, d.secret),
            el("p", { class: "hint" }, "Ou use o URI: ", el("code", {}, d.otpauth_uri)), el("div", { class: "filters" }, code,
              el("button", { class: "btn primary", onclick: async () => { try { await api("/api/me/mfa/enable", { method: "POST", body: { code: code.value } }); S.user.mfa_enabled = true; paint(); toast("MFA ativada.", "ok"); } catch (e) { toast(e.message, "err"); } } }, "Confirmar")));
        } }, "Configurar MFA"));
    }
  };
  paint();
}

// ------------------------------------------------------------------ plataforma (superadmin)
async function viewAdminStations(c) {
  const slug = el("input", { placeholder: "minha-radio", id: "n-slug" }), name = el("input", { placeholder: "Minha Rádio", id: "n-name" }), owner = el("input", { type: "email", placeholder: "proprietário (opcional)", id: "n-owner" });
  c.append(pageH("Emissoras"), el("div", { class: "card", style: "margin-bottom:16px" }, el("h3", {}, "Nova emissora"),
    el("div", { class: "filters" }, slug, name, owner, el("button", { class: "btn primary", onclick: async () => {
      try { const s = await api("/api/stations", { method: "POST", body: { slug: slug.value, name: name.value, owner_email: owner.value || null } }); toast("Emissora criada.", "ok"); S.sid = s.id; localStorageSet("rssp.sid", s.id); boot(); }
      catch (e) { toast(e.message, "err"); }
    } }, "Criar")), el("p", { class: "hint" }, "Cada emissora tem biblioteca, fila, transmissão, equipe e relatórios isolados.")),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, ["Nome", "Endereço", "Seu papel"].map((h) => el("th", {}, h)))),
      el("tbody", {}, S.stations.map((s) => el("tr", {}, el("td", {}, s.name), el("td", {}, el("a", { href: `/r/${s.slug}`, target: "_blank", rel: "noopener" }, `/r/${s.slug}`)), el("td", {}, s.role_label)))))));
}
async function viewAdminUsers(c) {
  const d = await api("/api/admin/users");
  const email = el("input", { type: "email", placeholder: "email@exemplo.com" }), name = el("input", { placeholder: "Nome" });
  const sa = el("input", { type: "checkbox", style: "width:auto", id: "u-sa" });
  c.append(pageH("Usuários da plataforma"), el("div", { class: "card", style: "margin-bottom:16px" }, el("h3", {}, "Novo usuário"),
    el("div", { class: "filters" }, email, name, el("label", { for: "u-sa", style: "display:flex;gap:6px;align-items:center;margin:auto 0" }, sa, "superadmin"),
      el("button", { class: "btn primary", onclick: async () => {
        try { const r = await api("/api/admin/users", { method: "POST", body: { email: email.value, name: name.value, is_superadmin: sa.checked } });
          await modal("Usuário criado", el("div", {}, el("p", {}, "Senha temporária (exibida uma única vez):"), el("div", { class: "secret mono" }, r.temporary_password)), [{ label: "Fechar", class: "primary" }]); render(); }
        catch (e) { toast(e.message, "err"); }
      } }, "Criar"))),
    el("div", { class: "card" }, el("table", {}, el("thead", {}, el("tr", {}, ["Nome", "E-mail", "Perfil", "MFA", "Criado em"].map((h) => el("th", {}, h)))),
      el("tbody", {}, d.users.map((u) => el("tr", {}, el("td", {}, u.name), el("td", {}, u.email), el("td", {}, u.is_superadmin ? el("span", { class: "badge info" }, "superadmin") : "usuário"),
        el("td", {}, u.mfa_enabled ? el("span", { class: "badge ok" }, "ativa") : el("span", { class: "badge" }, "não")), el("td", { class: "num" }, fmtDate(u.created_at))))))));
}
const STATUS_LABEL = { implemented: ["ok", "Implementado"], partial: ["info", "Parcial"], credentials: ["warn", "Depende de credenciais"], planned: ["", "Planejado"] };
function moduleCard(m) {
  const [cls, label] = STATUS_LABEL[m.status];
  return el("div", { class: `card module-card ${m.status}` }, el("div", { style: "display:flex;gap:8px;align-items:center" }, el("b", {}, m.name), el("span", { class: `badge ${cls}` }, label), el("span", { class: "badge" }, `Fase ${m.phase}`)),
    m.done ? el("p", {}, el("b", {}, "Disponível: "), m.done) : null, m.missing ? el("p", {}, el("b", {}, "Pendente: "), m.missing) : null,
    m.status === "planned" && !m.missing ? el("p", {}, "Ainda não implementado. Nada nesta área é simulado.") : null);
}
async function viewModules(c) { c.append(pageH("Estado real dos módulos"), el("div", { class: "grid g-2" }, S.meta.modules.map(moduleCard))); }
async function viewModule(c, [id]) {
  const m = S.meta.modules.find((x) => x.id === id);
  c.append(pageH(m ? m.name : "Módulo"), m ? moduleCard(m) : el("p", {}, "Módulo desconhecido."),
    el("p", { class: "hint", style: "margin-top:12px" }, "Este recurso está identificado como indisponível nesta versão e não é simulado como operacional."));
}

// ------------------------------------------------------------------ início
(async () => { if (await refresh()) boot(); else showLogin(); })();
