"""Endpoints públicos (sem autenticação) para o player e para monitoramento externo.
Nunca expõem credenciais, host interno de ingestão ou dados de usuários."""
from __future__ import annotations

from starlette.responses import FileResponse

from ..db import now_iso
from ..storage import get_storage
from .common import Ctx, HTTPError, endpoint
from .operation import HEARTBEAT_STALE_S, _age_s
from .stations import FORMATS


def _station_by_slug(ctx: Ctx) -> dict:
    st = ctx.db.fetchone("SELECT * FROM stations WHERE slug = ? AND is_active = 1", (ctx.params["slug"],))
    if not st:
        raise HTTPError(404, "emissora não encontrada")
    return st


def _live(ctx: Ctx, st: dict) -> tuple[bool, dict]:
    ps = ctx.db.fetchone("SELECT * FROM playout_state WHERE station_id = ?", (st["id"],)) or {}
    age = _age_s(ps.get("heartbeat_at"))
    on_air = age is not None and age < HEARTBEAT_STALE_S and ps.get("status") == "on_air" and bool(ps.get("encoder_connected"))
    return on_air, ps


@endpoint
def public_station(ctx: Ctx):
    st = _station_by_slug(ctx)
    cfg = ctx.db.fetchone("SELECT public_listen_url, format, bitrate_kbps FROM stream_configs WHERE station_id = ?",
                          (st["id"],))
    on_air, ps = _live(ctx, st)
    now = None
    if on_air and ps.get("now_title"):
        m = ctx.db.fetchone("SELECT kind, cover_key FROM media WHERE id = ? AND station_id = ?",
                            (ps.get("now_media_id"), st["id"])) if ps.get("now_media_id") else None
        now = {"title": ps["now_title"], "artist": ps.get("now_artist"), "started_at": ps.get("now_started_at"),
               "duration_ms": ps.get("now_duration_ms"), "kind": m["kind"] if m else None,
               "cover_url": f"/api/public/stations/{st['slug']}/cover/{ps['now_media_id']}" if m and m["cover_key"] else None}
    history = []
    if st["show_history"]:
        rows = ctx.db.fetchall("SELECT p.title, p.artist, p.started_at, p.media_id, m.cover_key FROM play_log p "
                               "LEFT JOIN media m ON m.id = p.media_id WHERE p.station_id = ? AND p.kind = 'music' "
                               "AND p.ended_at IS NOT NULL ORDER BY p.started_at DESC LIMIT 10", (st["id"],))
        history = [{"title": r["title"], "artist": r["artist"], "started_at": r["started_at"],
                    "cover_url": f"/api/public/stations/{st['slug']}/cover/{r['media_id']}" if r["cover_key"] else None}
                   for r in rows]
    return {"slug": st["slug"], "name": st["name"], "slogan": st["slogan"], "description": st["description"],
            "primary_color": st["primary_color"], "timezone": st["timezone"],
            "stream": {"url": cfg["public_listen_url"], "content_type": FORMATS.get(cfg["format"]),
                       "format": cfg["format"], "bitrate_kbps": cfg["bitrate_kbps"]},
            "on_air": on_air, "now_playing": now, "history": history, "server_time": now_iso()}


@endpoint
def public_status(ctx: Ctx):
    """Endpoint de status para monitores de disponibilidade (ex.: UptimeRobot)."""
    st = _station_by_slug(ctx)
    on_air, ps = _live(ctx, st)
    return {"slug": st["slug"], "on_air": on_air, "listeners": ps.get("listeners") if on_air else None,
            "heartbeat_age_s": _age_s(ps.get("heartbeat_at")), "server_time": now_iso()}


@endpoint
def public_cover(ctx: Ctx):
    st = _station_by_slug(ctx)
    m = ctx.db.fetchone("SELECT cover_key FROM media WHERE id = ? AND station_id = ?", (ctx.params["mid"], st["id"]))
    if not m or not m["cover_key"]:
        raise HTTPError(404, "sem capa")
    return FileResponse(get_storage().local_path(m["cover_key"]), media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})
