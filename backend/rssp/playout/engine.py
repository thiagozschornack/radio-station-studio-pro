"""Motor de playout — serviço de backend independente do navegador.

Arquitetura (por emissora):

  Controller (thread principal) ── decide o que tocar, registra execuções, lê comandos, heartbeat (banco)
        │ set_next(item) / skip()                         ▲ eventos started/ended
        ▼                                                 │
  Pump (thread)  ── lê PCM 48 kHz/16 bit/estéreo dos decodificadores FFmpeg, troca de faixa sem lacuna,
        │           cadencia em tempo real e preenche com silêncio se faltar conteúdo (nunca acessa o banco)
        ▼
  Encoder (processo FFmpeg) ── PCM → MP3/AAC/Opus ── Sender (thread) ── HTTP PUT → Icecast
                                                       reconecta com backoff sem parar o relógio de áudio
"""
from __future__ import annotations

import logging
import os
import queue
import random
import select
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field

from ..config import get_settings
from ..db import Database, get_db, new_id, now_iso, parse_iso
from ..media import gain_for
from ..security import decrypt
from ..storage import get_storage
from .icecast import IcecastError, SourceConfig, connect_source, fetch_listeners, update_metadata

log = logging.getLogger("rssp.playout")

SR, CH, BYTES_PER_SAMPLE = 48000, 2, 2
BPS = SR * CH * BYTES_PER_SAMPLE          # bytes de PCM por segundo
CHUNK = BPS // 10                          # 100 ms
LEAD_S = 0.4                               # quanto o PCM pode adiantar-se ao relógio real
CONTENT_TYPES = {"mp3": "audio/mpeg", "aac": "audio/aac", "opus": "audio/ogg"}
HEARTBEAT_S = 2.0
LISTENERS_S = 15.0
STALE_S = 15.0


# =============================================================================== itens
@dataclass
class PlayItem:
    media: dict
    source: str                      # manual | rotation | fallback
    queue_item_id: str | None = None
    proc: subprocess.Popen | None = None
    bytes_read: int = 0
    started_wall: str | None = None
    play_id: str | None = None
    planned_ms: int = 0
    gain_db: float = 0.0
    carry: bytes = b""
    extra: dict = field(default_factory=dict)

    @property
    def played_ms(self) -> int:
        return int(self.bytes_read * 1000 / BPS)

    def label(self) -> str:
        a = self.media.get("artist")
        return f"{a} - {self.media['title']}" if a else self.media["title"]

    def spawn(self, path: str, normalize: bool, target_lufs: float) -> None:
        s = get_settings()
        m = self.media
        cue_in = (m.get("cue_in_ms") or 0) / 1000
        cue_out = (m.get("cue_out_ms") or m["duration_ms"]) / 1000
        dur = max(0.5, cue_out - cue_in)
        self.planned_ms = int(dur * 1000)
        self.gain_db = gain_for(m.get("loudness_lufs"), target_lufs, m.get("true_peak_db")) if normalize else 0.0
        filters = [f"volume={self.gain_db:.2f}dB"]
        fi, fo = (m.get("fade_in_ms") or 0) / 1000, (m.get("fade_out_ms") or 0) / 1000
        if fi > 0:
            filters.append(f"afade=t=in:st=0:d={fi:.3f}")
        if fo > 0:
            filters.append(f"afade=t=out:st={max(0, dur - fo):.3f}:d={fo:.3f}")
        filters.append("alimiter=limit=0.97:level=false")
        cmd = [s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if cue_in > 0:
            cmd += ["-ss", f"{cue_in:.3f}"]
        cmd += ["-t", f"{dur:.3f}", "-i", path, "-vn", "-af", ",".join(filters),
                "-f", "s16le", "-acodec", "pcm_s16le", "-ar", str(SR), "-ac", str(CH), "pipe:1"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        if self.proc:
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            if self.proc.stdout:
                self.proc.stdout.close()


# =============================================================================== encoder + sender
class Encoder:
    """Processo FFmpeg que codifica PCM e uma thread que envia o resultado ao Icecast."""

    def __init__(self, on_status):
        self.cfg: SourceConfig | None = None
        self.fmt = "mp3"
        self.proc: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self._pending_sock: socket.socket | None = None
        self.lock = threading.Lock()
        self.connected = False
        self.connected_since: str | None = None
        self.last_error: str | None = None
        self.reconnects = 0
        self._restart_needed = False
        self._stop = threading.Event()
        self._connecting = False
        self._next_attempt = 0.0
        self._backoff = 1.0
        self.on_status = on_status
        self.bytes_sent = 0

    # ------------------------------------------------------------------ ciclo de vida
    def configure(self, cfg: SourceConfig, fmt: str) -> None:
        with self.lock:
            self.cfg, self.fmt = cfg, fmt
            self._restart_needed = True
            self._drop_socket("reconfiguração")
            self._next_attempt = 0.0

    def stop(self) -> None:
        with self.lock:
            self._drop_socket(None)
            self._kill_proc()
            self.cfg = None

    def _kill_proc(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    def _spawn(self) -> None:
        s = get_settings()
        cfg = self.cfg
        assert cfg is not None
        codec = {"mp3": ["-c:a", "libmp3lame", "-f", "mp3"],
                 "aac": ["-c:a", "aac", "-f", "adts"],
                 "opus": ["-c:a", "libopus", "-application", "audio", "-f", "ogg", "-page_duration", "200000"]}[self.fmt]
        cmd = [s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-f", "s16le", "-ar", str(SR), "-ac", str(CH),
               "-i", "pipe:0", "-ar", str(cfg.sample_rate), "-ac", str(cfg.channels), "-b:a", f"{cfg.bitrate}k",
               *codec, "-flush_packets", "1", "pipe:1"]
        self._kill_proc()
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        if self._pending_sock is not None:
            # A conexão nova só recebe a saída do encoder novo: o fluxo começa com cabeçalhos válidos.
            self.sock, self._pending_sock = self._pending_sock, None
            self.connected = True
            self.connected_since = now_iso()
            self.last_error = None
        threading.Thread(target=self._reader, args=(self.proc,), daemon=True, name="encoder-reader").start()
        self._restart_needed = False

    # ------------------------------------------------------------------ escrita de PCM (thread da pump)
    def write(self, pcm: bytes) -> None:
        with self.lock:
            if self.cfg is None:
                return
            if self.proc is None or self.proc.poll() is not None or self._restart_needed:
                if self.proc is not None and self.proc.poll() is not None:
                    self.last_error = "encoder FFmpeg encerrou; reiniciando"
                self._spawn()
            proc = self.proc
        try:
            proc.stdin.write(pcm)
            proc.stdin.flush()
        except (BrokenPipeError, ValueError, OSError):
            with self.lock:
                self._restart_needed = True

    # ------------------------------------------------------------------ envio (thread por processo encoder)
    def _reader(self, proc: subprocess.Popen) -> None:
        fd = proc.stdout.fileno()
        while True:
            try:
                data = os.read(fd, 16384)
            except OSError:
                break
            if not data:
                break
            self._maybe_connect()
            sock = self.sock
            if sock is not None and proc is self.proc:
                try:
                    sock.sendall(data)
                    self.bytes_sent += len(data)
                except OSError as e:
                    with self.lock:
                        if self.sock is sock:
                            self._drop_socket(f"conexão perdida: {e}")
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _maybe_connect(self) -> None:
        if (self.sock is not None or self._pending_sock is not None or self._connecting or self.cfg is None
                or time.monotonic() < self._next_attempt):
            return
        self._connecting = True
        threading.Thread(target=self._connect, daemon=True, name="icecast-connect").start()

    def _connect(self) -> None:
        cfg = self.cfg
        try:
            if cfg is None:
                return
            sock = connect_source(cfg)
            with self.lock:
                if self.cfg is not cfg:
                    sock.close()
                    return
                self._pending_sock = sock
                self._backoff = 1.0
                # Reinicia o encoder; a conexão é entregue a ele em _spawn (essencial para Ogg/Opus).
                self._restart_needed = True
            log.info("conectado ao Icecast %s:%s%s", cfg.host, cfg.port, cfg.mount)
            self.on_status()
        except (IcecastError, OSError) as e:
            with self.lock:
                self.last_error = str(e)
                self._next_attempt = time.monotonic() + self._backoff
                self._backoff = min(30.0, self._backoff * 2)
                self.reconnects += 1
            log.warning("falha ao conectar ao Icecast: %s (nova tentativa em %.0fs)", e, self._backoff / 2)
            self.on_status()
        finally:
            self._connecting = False

    def _drop_socket(self, reason: str | None) -> None:
        if self._pending_sock is not None:
            try:
                self._pending_sock.close()
            except OSError:
                pass
            self._pending_sock = None
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        if self.connected and reason:
            self.reconnects += 1
            log.warning("Icecast: %s", reason)
        self.connected = False
        self.connected_since = None
        if reason:
            self.last_error = reason


# =============================================================================== pump
class Pump(threading.Thread):
    """Gera o fluxo contínuo de PCM em tempo real. Não toca no banco de dados."""

    def __init__(self, encoder: Encoder, events: queue.Queue):
        super().__init__(daemon=True, name="pump")
        self.encoder = encoder
        self.events = events
        self.lock = threading.Lock()
        self.current: PlayItem | None = None
        self.next: PlayItem | None = None
        self.running = threading.Event()
        self.stopped = threading.Event()
        self.underruns = 0
        self.held_ms = 0
        self.lead_ms = 0
        self._skip = False

    def set_next(self, item: PlayItem) -> None:
        with self.lock:
            if self.next is not None:
                self.next.kill()
            self.next = item

    def drop_next(self) -> PlayItem | None:
        with self.lock:
            n, self.next = self.next, None
        if n:
            n.kill()
        return n

    def skip(self) -> None:
        with self.lock:
            self._skip = True

    def stop_all(self, reason: str) -> None:
        with self.lock:
            cur, nxt = self.current, self.next
            self.current = self.next = None
        if cur:
            cur.extra["ended_wall"] = now_iso()
            cur.kill()
            self.events.put(("ended", cur, reason))
        if nxt:
            nxt.kill()

    def shutdown(self) -> None:
        self.stopped.set()

    # ------------------------------------------------------------------ laço de tempo real
    def run(self) -> None:
        start = time.monotonic()
        written = 0
        while not self.stopped.is_set():
            if not self.running.is_set():
                time.sleep(0.05)
                start, written = time.monotonic(), 0
                continue
            now = time.monotonic()
            target = (now - start + LEAD_S) * BPS
            if target - written > 2 * BPS:  # travamento longo: não despeja rajada, realinha o relógio
                start, written = now, 0
                target = LEAD_S * BPS
            if written >= target:
                self.lead_ms = int((written / BPS - (now - start)) * 1000)
                time.sleep(0.01)
                continue
            chunk = self._fill(CHUNK)
            self.encoder.write(chunk)
            written += len(chunk)

    def _advance(self, reason: str) -> None:
        """Encerra a faixa atual e promove a próxima (sem lacuna)."""
        with self.lock:
            cur, self.current = self.current, self.next
            self.next = None
            self._skip = False
            new = self.current
        if cur:
            cur.extra["ended_wall"] = now_iso()
            cur.kill()
            self.events.put(("ended", cur, reason))
        if new:
            new.started_wall = now_iso()
            self.events.put(("started", new, None))

    def _fill(self, size: int) -> bytes:
        if not self.encoder.connected:
            # Sem servidor de streaming ninguém ouve: segura a faixa (não avança nem registra execução)
            # e envia silêncio ao encoder apenas para mantê-lo vivo enquanto reconecta.
            self.held_ms += size * 1000 // BPS
            return bytes(size)
        buf = bytearray()
        deadline = time.monotonic() + 0.25
        while len(buf) < size:
            with self.lock:
                skip = self._skip
                cur = self.current
                has_next = self.next is not None
            if skip:
                self._advance("skipped")
                continue
            if cur is None:
                if has_next:
                    self._advance("none")
                    continue
                buf += bytes(size - len(buf))  # silêncio (sem conteúdo disponível)
                self.underruns += 1
                break
            fd = cur.proc.stdout.fileno()
            r, _, _ = select.select([fd], [], [], max(0.0, deadline - time.monotonic()))
            if not r:
                # decodificador lento: completa com silêncio para não interromper o fluxo
                buf += bytes(size - len(buf))
                self.underruns += 1
                break
            try:
                data = os.read(fd, size - len(buf))
            except OSError:
                data = b""
            if not data:
                code = cur.proc.poll()
                if code is None:
                    try:
                        code = cur.proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        code = -1
                ok = code == 0 and cur.bytes_read > 0
                if not ok:
                    cur.extra["error"] = f"decodificador terminou com código {code}"
                self._advance("completed" if ok else "error")
                continue
            data = cur.carry + data          # mantém alinhamento por quadro (4 bytes)
            keep = len(data) - (len(data) % 4)
            cur.carry, data = data[keep:], data[:keep]
            cur.bytes_read += len(data)
            buf += data
        return bytes(buf[:size]) if len(buf) >= size else bytes(buf) + bytes(size - len(buf))


# =============================================================================== controller
class StationEngine:
    def __init__(self, db: Database, station_id: str):
        self.db = db
        self.station_id = station_id
        self.session_id = new_id()
        self.events: queue.Queue = queue.Queue()
        self.listener_q: queue.Queue = queue.Queue()
        self.encoder = Encoder(on_status=lambda: None)
        self.pump = Pump(self.encoder, self.events)
        self.stop_flag = threading.Event()
        self.cfg_row: dict | None = None
        self.enabled = False
        self._last_hb = 0.0
        self._last_listeners = 0.0
        self.listeners: int | None = None
        self.listeners_at: str | None = None
        self.status = "starting"
        self.source_cfg: SourceConfig | None = None
        self._meta_key: tuple | None = None

    # ------------------------------------------------------------------ posse da emissora
    def acquire(self) -> bool:
        """Garante uma única instância de playout por emissora (lease via heartbeat)."""
        now = now_iso()
        self.db.execute("INSERT INTO playout_state (station_id, status, updated_at) SELECT ?, 'offline', ? "
                        "WHERE NOT EXISTS (SELECT 1 FROM playout_state WHERE station_id = ?)",
                        (self.station_id, now, self.station_id))
        cur = self.db.fetchone("SELECT session_id, pid, hostname FROM playout_state WHERE station_id = ?",
                               (self.station_id,))
        dead_owner = None
        if cur and cur["session_id"] and cur["hostname"] == socket.gethostname() and cur["pid"]:
            # Dono anterior na mesma máquina com processo inexistente (ex.: kill -9): assume sem esperar o lease.
            try:
                os.kill(int(cur["pid"]), 0)
            except ProcessLookupError:
                dead_owner = cur["session_id"]
            except PermissionError:
                pass
        n = self.db.execute(
            "UPDATE playout_state SET session_id = ?, pid = ?, hostname = ?, status = 'starting', heartbeat_at = ?, "
            "updated_at = ? WHERE station_id = ? AND (heartbeat_at IS NULL OR heartbeat_at < ? OR session_id IS NULL "
            "OR session_id = ?)",
            (self.session_id, os.getpid(), socket.gethostname(), now, now, self.station_id, now_iso(-STALE_S),
             dead_owner or "-"))
        if n == 1 and dead_owner:
            log.warning("instância anterior (pid %s) não existe mais; assumindo a emissora", cur["pid"])
        return n == 1

    def recover(self) -> None:
        """Após reinício: fecha execuções abertas e devolve o item manual interrompido ao topo da fila."""
        ps = self.db.fetchone("SELECT heartbeat_at FROM playout_state WHERE station_id = ?", (self.station_id,))
        open_plays = self.db.fetchall("SELECT * FROM play_log WHERE station_id = ? AND ended_at IS NULL", (self.station_id,))
        for p in open_plays:
            end = (ps or {}).get("heartbeat_at") or now_iso()
            if end < p["started_at"]:
                end = p["started_at"]
            played = int((parse_iso(end) - parse_iso(p["started_at"])).total_seconds() * 1000)
            self.db.update("play_log", {"ended_at": end, "played_ms": max(0, played), "end_reason": "interrupted"},
                           "id = ?", (p["id"],))
            log.info("execução interrompida recuperada: %s", p["title"])
        self._requeue_playing()

    def _requeue_playing(self) -> None:
        playing = self.db.fetchall("SELECT * FROM queue_items WHERE station_id = ? AND status = 'playing'", (self.station_id,))
        if not playing:
            return
        top = self.db.scalar("SELECT MIN(position) AS p FROM queue_items WHERE station_id = ? AND status = 'pending'",
                             (self.station_id,))
        top = (top if top is not None else 0) - 1
        for q in playing:
            if q["source"] == "manual":
                self.db.update("queue_items", {"status": "pending", "position": top, "started_at": None},
                               "id = ?", (q["id"],))
                top -= 1
            else:
                self.db.update("queue_items", {"status": "played", "ended_at": now_iso()}, "id = ?", (q["id"],))

    # ------------------------------------------------------------------ configuração
    def load_config(self) -> None:
        c = self.db.fetchone("SELECT * FROM stream_configs WHERE station_id = ?", (self.station_id,))
        st = self.db.fetchone("SELECT name, slug FROM stations WHERE id = ?", (self.station_id,))
        self.cfg_row = c
        pw = decrypt(c["source_password_enc"])
        want = bool(c["enabled"]) and bool(pw)
        new_cfg = None
        if pw:
            new_cfg = SourceConfig(host=c["host"], port=c["port"], mount=c["mount"], user=c["source_user"] or "source",
                                   password=pw, use_tls=bool(c["use_tls"]), content_type=CONTENT_TYPES[c["format"]],
                                   name=c["stream_name"] or st["name"], genre=c["stream_genre"] or "",
                                   description=c["stream_description"] or "",
                                   url=get_settings().public_base_url.rstrip("/") + f"/r/{st['slug']}",
                                   bitrate=c["bitrate_kbps"], sample_rate=c["sample_rate"], channels=c["channels"])
        changed = new_cfg != self.source_cfg or (want != self.enabled)
        if want and changed:
            self.source_cfg = new_cfg
            self.encoder.configure(new_cfg, c["format"])
            if not self.enabled:
                log.info("transmissão ativada")
            self.enabled = True
            self.pump.running.set()
        elif not want and self.enabled:
            log.info("transmissão desativada")
            self.enabled = False
            self.pump.running.clear()
            self.pump.stop_all("stopped")
            self.encoder.stop()
            self.source_cfg = new_cfg
        elif not want:
            self.source_cfg = new_cfg

    # ------------------------------------------------------------------ seleção de conteúdo
    def _playable(self, m: dict | None) -> bool:
        if not m or not m["is_active"] or m["license_status"] == "blocked":
            return False
        if m["license_expires_at"] and m["license_expires_at"] < now_iso()[:10]:
            return False
        return get_storage().exists(m["storage_key"])

    def _first_pending(self) -> tuple[dict, dict] | None:
        rows = self.db.fetchall("SELECT * FROM queue_items WHERE station_id = ? AND status = 'pending' "
                                "ORDER BY position LIMIT 20", (self.station_id,))
        for q in rows:
            m = self.db.fetchone("SELECT * FROM media WHERE id = ? AND station_id = ?", (q["media_id"], self.station_id))
            if self._playable(m):
                return q, m
            self.db.update("queue_items", {"status": "failed", "ended_at": now_iso()}, "id = ?", (q["id"],))
            log.warning("item da fila descartado (mídia indisponível/bloqueada): %s", q["id"])
        return None

    def _rotation_pick(self) -> dict | None:
        c = self.cfg_row
        window = c["no_repeat_window"] if c else 10
        recent = [r["media_id"] for r in self.db.fetchall(
            "SELECT media_id FROM play_log WHERE station_id = ? ORDER BY started_at DESC LIMIT ?",
            (self.station_id, window))]
        cur = self.pump.current
        if cur:
            recent.append(cur.media["id"])
        cands = [m for m in self.db.fetchall("SELECT * FROM media WHERE station_id = ? AND kind = 'music' AND is_active = 1",
                                             (self.station_id,)) if self._playable(m)]
        if not cands:
            return None
        fresh = [m for m in cands if m["id"] not in recent]
        pool = fresh or [m for m in cands if not cur or m["id"] != cur.media["id"]] or cands
        return random.choice(pool)

    def ensure_next(self) -> None:
        if not self.enabled:
            return
        first = self._first_pending()
        nxt = self.pump.next
        if nxt is not None:
            # Prioridade editorial: a fila manual sempre prevalece sobre a rotação.
            if first and nxt.queue_item_id == first[0]["id"]:
                return
            if not first and nxt.source != "manual":
                return
            self.pump.drop_next()
        item = None
        if first:
            q, m = first
            item = PlayItem(media=m, source="manual", queue_item_id=q["id"])
        elif self.cfg_row and self.cfg_row["rotation_fallback"]:
            m = self._rotation_pick()
            if m:
                item = PlayItem(media=m, source="rotation")
        if item is None and self.cfg_row and self.cfg_row["fallback_media_id"]:
            m = self.db.fetchone("SELECT * FROM media WHERE id = ? AND station_id = ?",
                                 (self.cfg_row["fallback_media_id"], self.station_id))
            if self._playable(m):
                item = PlayItem(media=m, source="fallback")
        if item is None:
            return
        try:
            item.spawn(get_storage().local_path(item.media["storage_key"]), bool(self.cfg_row["normalize"]),
                       float(self.cfg_row["target_lufs"]))
        except OSError as e:
            log.error("falha ao iniciar decodificador: %s", e)
            return
        self.pump.set_next(item)

    # ------------------------------------------------------------------ eventos da pump
    def handle_events(self) -> None:
        while True:
            try:
                kind, item, reason = self.events.get_nowait()
            except queue.Empty:
                return
            if kind == "started":
                self._on_started(item)
            elif kind == "ended":
                self._on_ended(item, reason)

    def _on_started(self, item: PlayItem) -> None:
        m = item.media
        item.play_id = new_id()
        self.db.insert("play_log", {"id": item.play_id, "station_id": self.station_id, "media_id": m["id"],
                                    "queue_item_id": item.queue_item_id, "title": m["title"], "artist": m.get("artist"),
                                    "kind": m.get("kind"), "isrc": m.get("isrc"), "source": item.source,
                                    "started_at": item.started_wall, "ended_at": None, "planned_ms": item.planned_ms,
                                    "played_ms": None, "end_reason": None, "session_id": self.session_id})
        if item.queue_item_id:
            self.db.update("queue_items", {"status": "playing", "started_at": item.started_wall}, "id = ?",
                           (item.queue_item_id,))
        self.db.update("playout_state", {"now_media_id": m["id"], "now_play_id": item.play_id, "now_title": m["title"],
                                         "now_artist": m.get("artist"), "now_source": item.source,
                                         "now_started_at": item.started_wall, "now_duration_ms": item.planned_ms,
                                         "updated_at": now_iso()}, "station_id = ?", (self.station_id,))
        log.info("no ar: %s [%s] ganho %.1f dB", item.label(), item.source, item.gain_db)
        self.sync_metadata()

    def sync_metadata(self) -> None:
        """Envia o título atual ao Icecast quando muda a faixa ou a conexão (reconexão)."""
        cfg, cur = self.source_cfg, self.pump.current
        if not (cfg and cur and self.encoder.connected and self.cfg_row and self.cfg_row["format"] in ("mp3", "aac")):
            return
        key = (cur.play_id, self.encoder.connected_since)
        if key == self._meta_key or not cur.play_id:
            return
        self._meta_key = key
        threading.Thread(target=self._send_meta, args=(cfg, cur.label()), daemon=True).start()

    def _send_meta(self, cfg: SourceConfig, song: str) -> None:
        try:
            update_metadata(cfg, song)
        except Exception as e:  # noqa: BLE001
            log.warning("metadados não atualizados no Icecast: %s", e)

    def _on_ended(self, item: PlayItem, reason: str) -> None:
        if item.play_id:
            self.db.update("play_log", {"ended_at": item.extra.get("ended_wall") or now_iso(),
                                        "played_ms": item.played_ms, "end_reason": reason},
                           "id = ?", (item.play_id,))
        if item.queue_item_id:
            if reason in ("stopped", "interrupted") and item.source == "manual":
                self._requeue_playing()
            else:
                st = {"completed": "played", "skipped": "skipped", "error": "failed"}.get(reason, "played")
                self.db.update("queue_items", {"status": st, "ended_at": now_iso()}, "id = ?", (item.queue_item_id,))
        if reason == "error":
            log.error("erro ao reproduzir %s: %s", item.label(), item.extra.get("error"))
        if self.pump.current is None:
            self.db.update("playout_state", {"now_media_id": None, "now_play_id": None, "now_title": None,
                                             "now_artist": None, "now_source": None, "now_started_at": None,
                                             "now_duration_ms": None, "updated_at": now_iso()},
                           "station_id = ?", (self.station_id,))

    # ------------------------------------------------------------------ comandos
    def handle_commands(self) -> None:
        cmds = self.db.fetchall("SELECT * FROM playout_commands WHERE station_id = ? AND status = 'pending' "
                                "ORDER BY created_at", (self.station_id,))
        for c in cmds:
            if c["command"] == "skip":
                if self.pump.current is None and self.pump.next is None:
                    pass
                else:
                    self.ensure_next()
                    self.pump.skip()
            elif c["command"] == "reload":
                self.load_config()
            self.db.update("playout_commands", {"status": "done", "processed_at": now_iso()}, "id = ?", (c["id"],))

    # ------------------------------------------------------------------ heartbeat / ouvintes
    def heartbeat(self) -> bool:
        if not self.enabled:
            self.status = "stopped" if self.source_cfg else "waiting_config"
        elif self.encoder.connected:
            self.status = "on_air" if self.pump.current else "dead_air"
        else:
            self.status = "connecting"
        nxt = self.pump.next
        try:
            load = round(os.getloadavg()[0] / (os.cpu_count() or 1), 2)
        except OSError:
            load = None
        n = self.db.execute(
            "UPDATE playout_state SET status = ?, encoder_connected = ?, encoder_since = ?, reconnects = ?, "
            "last_error = ?, next_media_id = ?, next_title = ?, listeners = ?, listeners_at = ?, host_load = ?, "
            "buffer_ms = ?, heartbeat_at = ?, updated_at = ? WHERE station_id = ? AND session_id = ?",
            (self.status, 1 if self.encoder.connected else 0, self.encoder.connected_since, self.encoder.reconnects,
             self.encoder.last_error, nxt.media["id"] if nxt else None, nxt.label() if nxt else None,
             self.listeners, self.listeners_at, load, self.pump.lead_ms, now_iso(), now_iso(),
             self.station_id, self.session_id))
        return n == 1

    def poll_listeners(self) -> None:
        cfg = self.source_cfg
        if not cfg or not self.enabled:
            self.listeners = None
            return
        status_url = self.cfg_row["status_url"] if self.cfg_row else None

        def work():
            try:
                n = fetch_listeners(cfg, status_url)
            except Exception as e:  # noqa: BLE001
                log.debug("status do Icecast indisponível: %s", e)
                return
            self.listeners, self.listeners_at = n, now_iso()
            self.listener_q.put(n)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ laço principal
    def run(self) -> None:
        if not self.acquire():
            raise SystemExit(f"outra instância de playout está ativa para a emissora {self.station_id}")
        self.recover()
        self.load_config()
        self.pump.start()
        log.info("playout iniciado (sessão %s)", self.session_id)
        try:
            while not self.stop_flag.is_set():
                self._drain_listener_events()
                self.handle_events()
                self.handle_commands()
                self.ensure_next()
                self.handle_events()
                self.sync_metadata()
                t = time.monotonic()
                if t - self._last_hb >= HEARTBEAT_S:
                    self._last_hb = t
                    self.load_config()
                    self.handle_events()
                    if not self.heartbeat():
                        log.error("outra instância assumiu esta emissora; encerrando")
                        break
                if t - self._last_listeners >= LISTENERS_S:
                    self._last_listeners = t
                    self.poll_listeners()
                time.sleep(0.2)
        finally:
            self.shutdown()

    def _drain_listener_events(self) -> None:
        # Amostras de ouvintes são gravadas aqui (thread do controller), não na thread HTTP.
        while True:
            try:
                n = self.listener_q.get_nowait()
            except queue.Empty:
                return
            self.db.insert("listener_snapshots", {"id": new_id(), "station_id": self.station_id, "taken_at": now_iso(),
                                                  "listeners": n, "peak": None, "source": "icecast"})

    def shutdown(self) -> None:
        log.info("encerrando playout…")
        self.pump.running.clear()
        self.pump.stop_all("interrupted")
        self.pump.shutdown()
        self.handle_events()
        self.encoder.stop()
        self.db.execute("UPDATE playout_state SET status = 'offline', encoder_connected = 0, heartbeat_at = NULL, "
                        "session_id = NULL, now_title = NULL, now_media_id = NULL, updated_at = ? "
                        "WHERE station_id = ? AND session_id = ?", (now_iso(), self.station_id, self.session_id))


# =============================================================================== entrada
def run_station(slug_or_id: str) -> None:
    db = get_db()
    db.migrate()
    st = db.fetchone("SELECT id, slug FROM stations WHERE slug = ? OR id = ?", (slug_or_id, slug_or_id))
    if not st:
        raise SystemExit(f"emissora não encontrada: {slug_or_id}")
    eng = StationEngine(db, st["id"])
    signal.signal(signal.SIGTERM, lambda *_: eng.stop_flag.set())
    signal.signal(signal.SIGINT, lambda *_: eng.stop_flag.set())
    eng.run()


def run_all() -> None:
    """Supervisor: um processo filho por emissora ativa, reiniciado em caso de falha."""
    import sys
    db = get_db()
    db.migrate()
    children: dict[str, subprocess.Popen] = {}
    backoff: dict[str, float] = {}
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        active = {r["id"]: r["slug"] for r in db.fetchall("SELECT id, slug FROM stations WHERE is_active = 1")}
        for sid, slug in active.items():
            p = children.get(sid)
            if p is None or p.poll() is not None:
                if p is not None:
                    log.warning("playout de %s saiu (código %s)", slug, p.returncode)
                if time.monotonic() < backoff.get(sid, 0):
                    continue
                children[sid] = subprocess.Popen([sys.executable, "-m", "rssp.playout", "--station", sid])
                backoff[sid] = time.monotonic() + 10
        for sid in list(children):
            if sid not in active:
                children.pop(sid).terminate()
        stop.wait(5)
    for p in children.values():
        p.terminate()
    for p in children.values():
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
