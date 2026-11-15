"""Operação: status da emissora (dashboard), fila de execução, comandos ao playout e relatórios."""
from __future__ import annotations

import csv
import io
import shutil
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from starlette.responses import Response

from ..config import get_settings
from ..db import new_id, now_iso, parse_iso
from .common import Ctx, HTTPError, endpoint
from .library import is_playable

HEARTBEAT_STALE_S = 15


def _age_s(iso: str | None) -> float | None:
    dt = parse_iso(iso)
    return (datetime.now(timezone.utc) - dt).total_seconds() if dt else None


def queue_rows(ctx: Ctx, sid: str, limit: int = 50) -> list[dict]:
    return ctx.db.fetchall(
        "SELECT q.id, q.media_id, q.position, q.status, q.source, q.created_at, m.title, m.artist, m.kind, "
        "m.duration_ms, m.cue_in_ms, m.cue_out_ms, m.license_status, m.is_active, m.license_expires_at "
        "FROM queue_items q JOIN media m ON m.id = q.media_id "
        "WHERE q.station_id = ? AND q.status = 'pending' ORDER BY q.position LIMIT ?", (sid, limit))


def _queue_out(r: dict) -> dict:
    ok, why = is_playable(r)
    eff = (r["cue_out_ms"] or r["duration_ms"]) - (r["cue_in_ms"] or 0)
    return {"id": r["id"], "media_id": r["media_id"], "title": r["title"], "artist": r["artist"], "kind": r["kind"],
            "effective_ms": eff, "source": r["source"], "position": r["position"], "playable": ok,
            "unplayable_reason": why}


@endpoint
def station_status(ctx: Ctx):
    st = ctx.station("station.view")
    sid = st["id"]
    ps = ctx.db.fetchone("SELECT * FROM playout_state WHERE station_id = ?", (sid,)) or {}
    cfg = ctx.db.fetchone("SELECT * FROM stream_configs WHERE station_id = ?", (sid,))
    hb_age = _age_s(ps.get("heartbeat_at"))
    service_online = hb_age is not None and hb_age < HEARTBEAT_STALE_S
    status = ps.get("status", "offline") if service_online else "offline"
    now_playing = None
    if service_online and ps.get("now_title"):
        elapsed = _age_s(ps.get("now_started_at")) or 0
        dur = ps.get("now_duration_ms") or 0
        now_playing = {"media_id": ps.get("now_media_id"), "title": ps.get("now_title"), "artist": ps.get("now_artist"),
                       "source": ps.get("now_source"), "started_at": ps.get("now_started_at"), "duration_ms": dur,
                       "remaining_ms": max(0, int(dur - elapsed * 1000))}
    queue = [_queue_out(r) for r in queue_rows(ctx, sid, 10)]
    tz = ZoneInfo(st["timezone"])
    local_midnight = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    day_start = local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    plays_today = ctx.db.scalar("SELECT COUNT(*) AS c FROM play_log WHERE station_id = ? AND started_at >= ?",
                                (sid, day_start))
    peak_today = ctx.db.scalar("SELECT MAX(listeners) AS m FROM listener_snapshots WHERE station_id = ? AND taken_at >= ?",
                               (sid, day_start))
    storage_bytes = ctx.db.scalar("SELECT COALESCE(SUM(size_bytes),0) AS s FROM media WHERE station_id = ?", (sid,))
    try:
        du = shutil.disk_usage(get_settings().storage_path)
        disk = {"total": du.total, "free": du.free}
    except OSError:
        disk = None

    alerts = []
    enabled = bool(cfg["enabled"])
    if not cfg["source_password_enc"]:
        alerts.append({"level": "warn", "text": "Servidor de streaming sem senha de source configurada."})
    if enabled and not service_online:
        alerts.append({"level": "critical", "text": "Transmissão ativada, mas o serviço de playout não responde."})
    elif enabled and service_online and not ps.get("encoder_connected"):
        alerts.append({"level": "critical", "text": "Playout ativo, mas sem conexão com o servidor de streaming: "
                       + (ps.get("last_error") or "reconectando…")})
    if not queue and not cfg["rotation_fallback"]:
        alerts.append({"level": "warn", "text": "Fila vazia e rotação de contingência desativada."})
    failed = ctx.db.scalar("SELECT COUNT(*) AS c FROM media WHERE station_id = ? AND analysis_status = 'failed'", (sid,))
    if failed:
        alerts.append({"level": "warn", "text": f"{failed} mídia(s) com falha de análise."})
    unknown = ctx.db.scalar("SELECT COUNT(*) AS c FROM media WHERE station_id = ? AND is_active = 1 "
                            "AND license_status = 'unknown'", (sid,))
    if unknown:
        alerts.append({"level": "info", "text": f"{unknown} mídia(s) ativas sem informação de licença."})
    soon = ctx.db.scalar("SELECT COUNT(*) AS c FROM media WHERE station_id = ? AND is_active = 1 AND "
                         "license_expires_at IS NOT NULL AND license_expires_at <= ?",
                         (sid, now_iso(30 * 86400)[:10]))
    if soon:
        alerts.append({"level": "warn", "text": f"{soon} licença(s) vencida(s) ou vencendo em até 30 dias."})
    if disk and disk["free"] / max(disk["total"], 1) < 0.1:
        alerts.append({"level": "critical", "text": "Menos de 10% de espaço livre no armazenamento."})

    return {
        "station": {"id": sid, "name": st["name"], "slug": st["slug"], "timezone": st["timezone"]},
        "status": status, "stream_enabled": enabled, "service_online": service_online,
        "heartbeat_age_s": round(hb_age, 1) if hb_age is not None else None,
        "encoder_connected": bool(ps.get("encoder_connected")) and service_online,
        "encoder_since": ps.get("encoder_since"), "reconnects": ps.get("reconnects") or 0,
        "last_error": ps.get("last_error"), "host_load": ps.get("host_load") if service_online else None,
        "buffer_ms": ps.get("buffer_ms") if service_online else None,
        "now_playing": now_playing,
        "next": queue[0] if queue else ({"title": ps.get("next_title"), "source": "rotation"}
                                        if service_online and ps.get("next_title") else None),
        "queue": queue,
        "listeners": {"current": ps.get("listeners") if service_online else None, "at": ps.get("listeners_at"),
                      "peak_today": peak_today},
        "plays_today": plays_today, "storage_bytes": int(storage_bytes or 0), "disk": disk,
        "format": {"codec": cfg["format"], "bitrate_kbps": cfg["bitrate_kbps"], "sample_rate": cfg["sample_rate"],
                   "channels": cfg["channels"], "mount": cfg["mount"]},
        "public_listen_url": cfg["public_listen_url"],
        "revenue": {"available": False, "note": "Módulo comercial indisponível — previsto para a Fase 3."},
        "alerts": alerts, "server_time": now_iso(),
    }


# ------------------------------------------------------------------ fila
@endpoint
def get_queue(ctx: Ctx):
    st = ctx.station("queue.view")
    return {"items": [_queue_out(r) for r in queue_rows(ctx, st["id"], 200)]}


@endpoint
def add_to_queue(ctx: Ctx):
    st = ctx.station("queue.edit")
    sid = st["id"]
    mids = ctx.body.get("media_ids") or ([ctx.field("media_id")] if ctx.body.get("media_id") else [])
    if not mids or not isinstance(mids, list) or len(mids) > 100:
        raise HTTPError(422, "informe media_id ou media_ids (até 100)", "validation")
    where = ctx.body.get("where", "end")
    with ctx.db.transaction(immediate=True):
        if where == "next":
            base = ctx.db.scalar("SELECT MIN(position) AS p FROM queue_items WHERE station_id = ? AND status = 'pending'",
                                 (sid,))
            start = (base if base is not None else 0) - len(mids)
        else:
            base = ctx.db.scalar("SELECT MAX(position) AS p FROM queue_items WHERE station_id = ?", (sid,))
            start = (base if base is not None else 0) + 1
        added = []
        for i, mid in enumerate(mids):
            m = ctx.db.fetchone("SELECT * FROM media WHERE id = ? AND station_id = ?", (mid, sid))
            if not m:
                raise HTTPError(404, "mídia não encontrada nesta emissora", "not_found")
            ok, why = is_playable(m)
            if not ok:
                raise HTTPError(409, f"“{m['title']}” não pode ir ao ar: {why}", "not_playable")
            qid = new_id()
            ctx.db.insert("queue_items", {"id": qid, "station_id": sid, "media_id": mid, "position": float(start + i),
                                          "status": "pending", "source": "manual", "added_by": ctx.user["id"],
                                          "created_at": now_iso(), "started_at": None, "ended_at": None})
            added.append(qid)
        ctx.audit("queue.added", sid, "queue", None, {"count": len(added), "where": where})
    return {"added": added}


@endpoint
def remove_from_queue(ctx: Ctx):
    st = ctx.station("queue.edit")
    n = ctx.db.execute("UPDATE queue_items SET status = 'removed', ended_at = ? WHERE id = ? AND station_id = ? "
                       "AND status = 'pending'", (now_iso(), ctx.params["qid"], st["id"]))
    if not n:
        raise HTTPError(404, "item não está pendente na fila")
    ctx.audit("queue.removed", st["id"], "queue", ctx.params["qid"])
    return {"ok": True}


@endpoint
def move_in_queue(ctx: Ctx):
    st = ctx.station("queue.edit")
    sid, qid = st["id"], ctx.params["qid"]
    idx = ctx.field("index", typ=int)
    with ctx.db.transaction(immediate=True):
        rows = ctx.db.fetchall("SELECT id FROM queue_items WHERE station_id = ? AND status = 'pending' ORDER BY position",
                               (sid,))
        ids = [r["id"] for r in rows]
        if qid not in ids:
            raise HTTPError(404, "item não está pendente na fila")
        ids.remove(qid)
        ids.insert(max(0, min(idx, len(ids))), qid)
        for i, x in enumerate(ids):
            ctx.db.update("queue_items", {"position": float(i)}, "id = ?", (x,))
        # Mantém novas inserções "no fim" depois dos reordenados.
    return {"order": ids}


@endpoint
def clear_queue(ctx: Ctx):
    st = ctx.station("queue.edit")
    n = ctx.db.execute("UPDATE queue_items SET status = 'removed', ended_at = ? WHERE station_id = ? AND status = 'pending'",
                       (now_iso(), st["id"]))
    ctx.audit("queue.cleared", st["id"], "queue", None, {"count": n})
    return {"removed": n}


@endpoint
def skip(ctx: Ctx):
    st = ctx.station("playout.skip")
    ctx.db.insert("playout_commands", {"id": new_id(), "station_id": st["id"], "command": "skip", "payload": None,
                                       "status": "pending", "created_by": ctx.user["id"], "created_at": now_iso(),
                                       "processed_at": None})
    ctx.audit("playout.skip", st["id"])
    return {"queued": True}


# ------------------------------------------------------------------ relatórios
def _range(ctx: Ctx) -> tuple[str, str]:
    frm = ctx.query.get("from") or now_iso(-86400)
    to = ctx.query.get("to") or now_iso(60)
    for v in (frm, to):
        if parse_iso_safe(v) is None:
            raise HTTPError(422, "datas em ISO-8601 UTC, ex.: 2026-11-15T00:00:00.000Z", "validation")
    return frm, to


def parse_iso_safe(v: str):
    try:
        return parse_iso(v)
    except ValueError:
        return None


@endpoint
def plays(ctx: Ctx):
    st = ctx.station("reports.view")
    frm, to = _range(ctx)
    rows = ctx.db.fetchall("SELECT * FROM play_log WHERE station_id = ? AND started_at >= ? AND started_at < ? "
                           "ORDER BY started_at DESC LIMIT 2000", (st["id"], frm, to))
    summary = {"total": len(rows), "completed": sum(1 for r in rows if r["end_reason"] == "completed"),
               "skipped": sum(1 for r in rows if r["end_reason"] == "skipped"),
               "interrupted": sum(1 for r in rows if r["end_reason"] in ("interrupted", "error")),
               "played_ms": sum(r["played_ms"] or 0 for r in rows)}
    return {"from": frm, "to": to, "items": rows, "summary": summary}


@endpoint
def plays_csv(ctx: Ctx):
    st = ctx.station("reports.view")
    frm, to = _range(ctx)
    rows = ctx.db.fetchall("SELECT * FROM play_log WHERE station_id = ? AND started_at >= ? AND started_at < ? "
                           "ORDER BY started_at", (st["id"], frm, to))
    tz = ZoneInfo(st["timezone"])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["inicio_utc", f"inicio_local ({st['timezone']})", "titulo", "artista", "isrc", "tipo", "origem",
                "tocado_segundos", "previsto_segundos", "encerramento"])
    for r in rows:
        local = parse_iso(r["started_at"]).astimezone(tz).strftime("%d/%m/%Y %H:%M:%S")
        w.writerow([r["started_at"], local, r["title"], r["artist"] or "", r["isrc"] or "", r["kind"] or "", r["source"],
                    round((r["played_ms"] or 0) / 1000), round((r["planned_ms"] or 0) / 1000), r["end_reason"] or "no ar"])
    ctx.audit("report.plays_exported", st["id"], details={"from": frm, "to": to, "rows": len(rows)})
    fname = f"execucoes-{st['slug']}-{frm[:10]}-a-{to[:10]}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@endpoint
def listeners(ctx: Ctx):
    st = ctx.station("reports.view")
    hours = max(1, min(24 * 31, int(ctx.query.get("hours", 24))))
    rows = ctx.db.fetchall("SELECT taken_at, listeners FROM listener_snapshots WHERE station_id = ? AND taken_at >= ? "
                           "ORDER BY taken_at", (st["id"], now_iso(-hours * 3600)))
    vals = [r["listeners"] for r in rows]
    return {"hours": hours, "points": rows, "peak": max(vals) if vals else None,
            "avg": round(sum(vals) / len(vals), 1) if vals else None, "samples": len(vals),
            "note": "Conexões simultâneas informadas pelo servidor Icecast (amostras a cada ~15 s). "
                    "Não equivalem a usuários únicos."}


@endpoint
def audit(ctx: Ctx):
    st = ctx.station("audit.view")
    rows = ctx.db.fetchall("SELECT a.*, u.email FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
                           "WHERE a.station_id = ? ORDER BY a.created_at DESC LIMIT 300", (st["id"],))
    return {"items": rows}
