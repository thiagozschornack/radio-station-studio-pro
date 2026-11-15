// Player público HTML5 — sem autoplay (respeita políticas dos navegadores), reconexão com backoff,
// Media Session (controles do sistema/tela de bloqueio) e metadados vindos da API pública.
const slug = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");
const $ = (id) => document.getElementById(id);
const audio = $("audio");
let info = null, wantPlay = false, retry = 0, retryTimer = null, lastTitle = null;

const setState = (txt, live = false) => { $("state").textContent = txt; $("eq").className = "eq" + (live ? "" : " paused"); };
function setIcons(playing) { $("ic-play").toggleAttribute("hidden", playing); $("ic-stop").toggleAttribute("hidden", !playing); $("play").setAttribute("aria-label", playing ? "Parar" : "Ouvir ao vivo"); }

async function load() {
  try {
    const r = await fetch(`/api/public/stations/${encodeURIComponent(slug)}`, { cache: "no-store" });
    if (!r.ok) { $("name").textContent = "Emissora não encontrada"; return; }
    info = await r.json();
  } catch { setState("Sem conexão com o servidor"); return; }
  document.title = `${info.name} — ao vivo`;
  document.documentElement.style.setProperty("--accent", info.primary_color || "#1f8bff");
  $("name").textContent = info.name; $("slogan").textContent = info.slogan || ""; $("desc").textContent = info.description || "";
  $("onair").className = "onair" + (info.on_air ? " live" : ""); $("onair").textContent = info.on_air ? "ON AIR" : "OFF AIR";
  const np = info.now_playing;
  $("title").textContent = np ? np.title : (info.on_air ? "Programação ao vivo" : "Emissora fora do ar no momento");
  $("artist").textContent = np?.artist || "";
  const art = $("art");
  if (np?.cover_url) { if (art.dataset.src !== np.cover_url) { art.dataset.src = np.cover_url; art.replaceChildren(Object.assign(new Image(), { src: np.cover_url, alt: "" })); } }
  else if (art.dataset.src) { delete art.dataset.src; art.innerHTML = art.dataset.svg; }
  if (!info.stream.url) {
    $("play").disabled = true; $("fmt").textContent = "Transmissão ainda não configurada pela emissora."; $("fmt").className = "fmt unavailable";
  } else {
    $("play").disabled = false;
    $("fmt").textContent = `${(info.stream.format || "").toUpperCase()} ${info.stream.bitrate_kbps} kbps`;
  }
  if (!wantPlay) setState(info.on_air ? "Ao vivo — toque para ouvir" : "Fora do ar");
  const key = np ? `${np.title}|${np.artist}` : null;
  if (key !== lastTitle) { lastTitle = key; updateMediaSession(np); }
  const hs = info.history || [];
  $("history-sec").hidden = !hs.length;
  $("history").replaceChildren(...hs.map((h) => {
    const li = document.createElement("li");
    const th = document.createElement("div"); th.className = "th";
    if (h.cover_url) th.append(Object.assign(new Image(), { src: h.cover_url, alt: "", loading: "lazy" }));
    const t = document.createElement("div"); t.className = "t";
    const b = document.createElement("b"); b.textContent = h.title; const s = document.createElement("span"); s.textContent = h.artist || "";
    t.append(b, s);
    const tm = document.createElement("time"); tm.dateTime = h.started_at;
    tm.textContent = new Date(h.started_at).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: info.timezone });
    li.append(th, t, tm); return li;
  }));
}
$("art").dataset.svg = $("art").innerHTML;

function updateMediaSession(np) {
  if (!("mediaSession" in navigator) || !info) return;
  const artwork = np?.cover_url ? [{ src: new URL(np.cover_url, location.href).href, sizes: "600x600", type: "image/jpeg" }] : [];
  navigator.mediaSession.metadata = new MediaMetadata({ title: np?.title || info.name, artist: np?.artist || info.name, album: info.name, artwork });
}
if ("mediaSession" in navigator) {
  navigator.mediaSession.setActionHandler("play", () => start());
  navigator.mediaSession.setActionHandler("pause", () => stop());
  navigator.mediaSession.setActionHandler("stop", () => stop());
}

function start() {
  if (!info?.stream.url) return;
  wantPlay = true; clearTimeout(retryTimer);
  // Parâmetro anti-cache: força nova conexão ao vivo (não reaproveita buffer antigo).
  const u = new URL(info.stream.url); u.searchParams.set("_", Date.now().toString(36));
  audio.src = u.href; audio.volume = Number($("volume").value);
  setState("Conectando…"); setIcons(true);
  audio.play().catch((e) => { if (e.name === "NotAllowedError") { wantPlay = false; setIcons(false); setState("Toque no play para ouvir"); } });
}
function stop() {
  wantPlay = false; clearTimeout(retryTimer); retry = 0;
  audio.pause(); audio.removeAttribute("src"); audio.load();
  setIcons(false); setState(info?.on_air ? "Ao vivo — toque para ouvir" : "Fora do ar");
}
function scheduleRetry(reason) {
  if (!wantPlay) return;
  const delay = Math.min(30000, 1000 * 2 ** retry++);
  setState(`${reason}; reconectando em ${Math.round(delay / 1000)}s…`);
  clearTimeout(retryTimer);
  retryTimer = setTimeout(() => { load().then(start); }, delay);
}
audio.addEventListener("playing", () => { retry = 0; setState("Ao vivo", true); });
audio.addEventListener("waiting", () => wantPlay && setState("Carregando…"));
audio.addEventListener("error", () => scheduleRetry("Sinal perdido"));
audio.addEventListener("ended", () => scheduleRetry("Transmissão interrompida"));
audio.addEventListener("stalled", () => { if (wantPlay && audio.readyState < 3) setTimeout(() => wantPlay && audio.readyState < 3 && scheduleRetry("Sinal instável"), 8000); });
$("play").addEventListener("click", () => (wantPlay ? stop() : start()));
$("volume").addEventListener("input", (e) => { audio.volume = Number(e.target.value); try { localStorage.setItem("rssp.vol", e.target.value); } catch { /* */ } });
try { const v = localStorage.getItem("rssp.vol"); if (v) $("volume").value = v; } catch { /* */ }
$("share").addEventListener("click", async () => {
  const data = { title: info?.name || "Rádio", text: info?.slogan || "Ouça ao vivo", url: location.href };
  if (navigator.share) { try { await navigator.share(data); } catch { /* cancelado */ } }
  else { try { await navigator.clipboard.writeText(location.href); $("share").textContent = "Link copiado!"; setTimeout(() => ($("share").textContent = "Compartilhar"), 2000); } catch { /* */ } }
});
document.addEventListener("keydown", (e) => { if (e.code === "Space" && e.target === document.body) { e.preventDefault(); wantPlay ? stop() : start(); } });

load();
setInterval(load, 10000);
