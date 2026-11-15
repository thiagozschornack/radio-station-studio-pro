"""Biblioteca musical e sonora: upload validado, catálogo, edição não destrutiva, licenças."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from ..config import get_settings
from ..db import Database, dumps, new_id, now_iso
from ..jobs import enqueue
from ..media import ALLOWED_EXTENSIONS, MediaError, probe
from ..security import sign_media_url, verify_media_sig
from ..storage import get_storage
from .common import Ctx, HTTPError, endpoint, run

KINDS = {"music": "Música", "jingle": "Jingle/Vinheta", "spot": "Comercial", "promo": "Chamada",
         "bed": "Trilha", "effect": "Efeito", "interview": "Entrevista", "podcast": "Podcast",
         "news": "Notícia", "other": "Outro"}
LICENSE_STATUSES = {"unknown": "Não informada", "licensed": "Licenciada", "own": "Produção própria",
                    "public_domain": "Domínio público", "creative_commons": "Creative Commons",
                    "blocked": "Bloqueada"}


def is_playable(m: dict) -> tuple[bool, str | None]:
    if not m["is_active"]:
        return False, "mídia arquivada"
    if m["license_status"] == "blocked":
        return False, "mídia bloqueada administrativamente (licença)"
    if m["license_expires_at"] and m["license_expires_at"] < now_iso()[:10]:
        return False, "licença expirada"
    return True, None


def media_out(m: dict, with_urls: bool = True) -> dict:
    out = {k: m[k] for k in ("id", "kind", "title", "artist", "album", "genre", "year", "bpm", "rating",
                             "original_filename", "container", "size_bytes", "duration_ms", "codec", "bitrate_kbps",
                             "sample_rate", "channels", "loudness_lufs", "true_peak_db", "lead_silence_ms",
                             "trail_silence_ms", "cue_in_ms", "cue_out_ms", "fade_in_ms", "fade_out_ms",
                             "analysis_status", "analysis_error", "license_status", "rights_holder", "license_ref",
                             "license_expires_at", "isrc", "created_at", "updated_at")}
    out["tags"] = json.loads(m["tags"] or "[]")
    out["is_active"] = bool(m["is_active"])
    out["is_demo"] = bool(m["is_demo"])
    out["has_cover"] = bool(m["cover_key"])
    ok, why = is_playable(m)
    out["playable"], out["unplayable_reason"] = ok, why
    out["effective_ms"] = (m["cue_out_ms"] or m["duration_ms"]) - (m["cue_in_ms"] or 0)
    return out


def _get_media(ctx: Ctx, st: dict) -> dict:
    m = ctx.db.fetchone("SELECT * FROM media WHERE id = ? AND station_id = ?", (ctx.params["mid"], st["id"]))
    if not m:
        raise HTTPError(404, "mídia não encontrada", "not_found")
    return m


@endpoint
def list_media(ctx: Ctx):
    st = ctx.station("library.view")
    where, params = ["station_id = ?"], [st["id"]]
    q = (ctx.query.get("q") or "").strip()
    if q:
        where.append("(LOWER(title) LIKE ? OR LOWER(artist) LIKE ? OR LOWER(album) LIKE ? OR LOWER(genre) LIKE ? "
                     "OR LOWER(tags) LIKE ?)")
        params += [f"%{q.lower()}%"] * 5
    kind = ctx.query.get("kind")
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if ctx.query.get("archived") != "1":
        where.append("is_active = 1")
    lic = ctx.query.get("license_status")
    if lic:
        where.append("license_status = ?")
        params.append(lic)
    order = {"title": "LOWER(title)", "artist": "LOWER(artist), LOWER(title)", "recent": "created_at DESC",
             "duration": "duration_ms DESC"}.get(ctx.query.get("sort", "recent"), "created_at DESC")
    limit = max(1, min(200, int(ctx.query.get("limit", 50))))
    offset = max(0, int(ctx.query.get("offset", 0)))
    w = " AND ".join(where)
    total = ctx.db.scalar(f"SELECT COUNT(*) AS c FROM media WHERE {w}", params)
    rows = ctx.db.fetchall(f"SELECT * FROM media WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?", params + [limit, offset])
    usage = ctx.db.scalar("SELECT COALESCE(SUM(size_bytes), 0) AS s FROM media WHERE station_id = ?", (st["id"],))
    return {"items": [media_out(r) for r in rows], "total": total, "limit": limit, "offset": offset,
            "storage_bytes": int(usage or 0), "kinds": KINDS, "license_statuses": LICENSE_STATUSES}


@endpoint
def get_media(ctx: Ctx):
    st = ctx.station("library.view")
    m = _get_media(ctx, st)
    plays = ctx.db.fetchall("SELECT started_at, played_ms, end_reason, source FROM play_log WHERE station_id = ? "
                            "AND media_id = ? ORDER BY started_at DESC LIMIT 20", (st["id"], m["id"]))
    count = ctx.db.scalar("SELECT COUNT(*) AS c FROM play_log WHERE station_id = ? AND media_id = ?", (st["id"], m["id"]))
    return media_out(m) | {"recent_plays": plays, "play_count": count}


def _int_or_none(ctx: Ctx, name: str, lo: int, hi: int) -> int | None:
    v = ctx.body.get(name)
    if v is None or v == "":
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise HTTPError(422, f"campo inválido: {name}", "validation")
    v = int(v)
    if not lo <= v <= hi:
        raise HTTPError(422, f"{name} fora do intervalo", "validation")
    return v


@endpoint
def update_media(ctx: Ctx):
    st = ctx.station("library.edit")
    m = _get_media(ctx, st)
    b, vals = ctx.body, {}
    for f, maxlen in (("title", 300), ("artist", 300), ("album", 300), ("genre", 100), ("isrc", 15),
                      ("rights_holder", 300), ("license_ref", 300)):
        if f in b:
            v = ctx.field(f, False, "")
            if f == "title" and not v:
                raise HTTPError(422, "título obrigatório", "validation")
            vals[f] = v[:maxlen] or None
    if "kind" in b:
        k = ctx.field("kind")
        if k not in KINDS:
            raise HTTPError(422, "tipo inválido", "validation")
        vals["kind"] = k
    if "year" in b:
        vals["year"] = _int_or_none(ctx, "year", 1800, 2200)
    if "rating" in b:
        vals["rating"] = _int_or_none(ctx, "rating", 1, 5)
    if "bpm" in b:
        v = b.get("bpm")
        if v in (None, ""):
            vals["bpm"] = None
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and 20 <= v <= 300:
            vals["bpm"] = float(v)
        else:
            raise HTTPError(422, "BPM entre 20 e 300", "validation")
    if "tags" in b:
        tags = b["tags"]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise HTTPError(422, "tags deve ser lista de textos", "validation")
        vals["tags"] = dumps(sorted({t.strip().lower()[:40] for t in tags if t.strip()})[:50])
    dur = m["duration_ms"]
    cue_in = _int_or_none(ctx, "cue_in_ms", 0, dur) if "cue_in_ms" in b else m["cue_in_ms"]
    cue_out = _int_or_none(ctx, "cue_out_ms", 0, dur) if "cue_out_ms" in b else m["cue_out_ms"]
    if "cue_in_ms" in b or "cue_out_ms" in b:
        cue_in = cue_in or 0
        if (cue_out or dur) - cue_in < 500:
            raise HTTPError(422, "o trecho entre entrada e saída deve ter ao menos 0,5 s", "validation")
        vals["cue_in_ms"], vals["cue_out_ms"] = cue_in, cue_out
    eff = (vals.get("cue_out_ms", m["cue_out_ms"]) or dur) - vals.get("cue_in_ms", m["cue_in_ms"])
    for f in ("fade_in_ms", "fade_out_ms"):
        if f in b:
            vals[f] = _int_or_none(ctx, f, 0, min(30000, eff // 2)) or 0
    license_fields = {"license_status", "license_expires_at", "rights_holder", "license_ref"}
    if license_fields & set(b):
        role = ctx.role_in(st["id"])
        from ..rbac import role_can
        if not ctx.user["is_superadmin"] and not role_can(role, "library.license"):
            raise HTTPError(403, "apenas proprietário/diretor alteram dados de licença", "forbidden")
    if "license_status" in b:
        ls = ctx.field("license_status")
        if ls not in LICENSE_STATUSES:
            raise HTTPError(422, "situação de licença inválida", "validation")
        vals["license_status"] = ls
    if "license_expires_at" in b:
        d = ctx.field("license_expires_at", False, "")
        if d and not (len(d) == 10 and d[4] == "-" and d[7] == "-"):
            raise HTTPError(422, "data de vigência no formato AAAA-MM-DD", "validation")
        vals["license_expires_at"] = d or None
    if "is_active" in b:
        vals["is_active"] = 1 if ctx.field("is_active", typ=bool) else 0
    if vals:
        vals["updated_at"] = now_iso()
        ctx.db.update("media", vals, "id = ? AND station_id = ?", (m["id"], st["id"]))
        ctx.audit("media.updated", st["id"], "media", m["id"], {"fields": sorted(vals)})
    return media_out(ctx.db.fetchone("SELECT * FROM media WHERE id = ?", (m["id"],)))


@endpoint
def delete_media(ctx: Ctx):
    st = ctx.station("library.edit")
    m = _get_media(ctx, st)
    used = ctx.db.scalar("SELECT COUNT(*) AS c FROM queue_items WHERE media_id = ?", (m["id"],))
    with ctx.db.transaction():
        if used:
            # Mantém o registro para preservar histórico de execução e comprovação.
            ctx.db.update("media", {"is_active": 0, "updated_at": now_iso()}, "id = ?", (m["id"],))
            ctx.db.execute("UPDATE queue_items SET status = 'removed' WHERE media_id = ? AND status = 'pending'", (m["id"],))
            ctx.audit("media.archived", st["id"], "media", m["id"], {"title": m["title"]})
            return {"archived": True}
        ctx.db.execute("DELETE FROM media WHERE id = ?", (m["id"],))
        ctx.audit("media.deleted", st["id"], "media", m["id"], {"title": m["title"]})
    get_storage().delete(m["storage_key"])
    if m["cover_key"]:
        get_storage().delete(m["cover_key"])
    return {"deleted": True}


@endpoint
def reanalyze_media(ctx: Ctx):
    st = ctx.station("library.edit")
    m = _get_media(ctx, st)
    ctx.db.update("media", {"analysis_status": "pending", "analysis_error": None}, "id = ?", (m["id"],))
    enqueue(ctx.db, "analyze_media", {"media_id": m["id"], "station_id": st["id"]}, st["id"])
    return {"queued": True}


@endpoint
def preview_url(ctx: Ctx):
    st = ctx.station("library.view")
    m = _get_media(ctx, st)
    return {"url": sign_media_url(st["id"], m["id"]), "expires_in": 600}


@endpoint
def media_cover(ctx: Ctx):
    st = ctx.station("library.view")
    m = _get_media(ctx, st)
    if not m["cover_key"]:
        raise HTTPError(404, "sem capa")
    return FileResponse(get_storage().local_path(m["cover_key"]), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})


@endpoint
def media_file(ctx: Ctx):
    """Pré-escuta no painel. Autorizada por URL assinada de curta duração (o <audio> não envia cabeçalhos)."""
    sid, mid = ctx.params["sid"], ctx.params["mid"]
    if not verify_media_sig(sid, mid, ctx.query.get("exp"), ctx.query.get("sig")):
        raise HTTPError(403, "link expirado", "forbidden")
    m = ctx.db.fetchone("SELECT storage_key, container FROM media WHERE id = ? AND station_id = ?", (mid, sid))
    if not m:
        raise HTTPError(404, "não encontrado")
    mt = {"mp3": "audio/mpeg", "wav": "audio/wav", "flac": "audio/flac", "ogg": "audio/ogg", "aac": "audio/aac",
          "aiff": "audio/aiff"}.get(m["container"], "audio/mp4")
    return FileResponse(get_storage().local_path(m["storage_key"]), media_type=mt,
                        headers={"Cache-Control": "private, no-store"})


# ------------------------------------------------------------------ upload
async def upload_media(request: Request) -> Response:
    s = get_settings()
    max_bytes = s.max_upload_mb * 1024 * 1024
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > max_bytes + 1_000_000:
        return JSONResponse({"error": f"arquivo excede {s.max_upload_mb} MB"}, status_code=413)

    pre = Ctx(request, None)
    auth = await run(pre, lambda c: c.station("library.upload") and {"ok": True})
    if auth.status_code != 200:
        return auth

    try:
        form = await request.form(max_files=1, max_fields=20)
    except Exception:
        return JSONResponse({"error": "formulário multipart inválido"}, status_code=400)
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        return JSONResponse({"error": "campo 'file' ausente"}, status_code=422)
    ext = Path(up.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse({"error": f"extensão não permitida: {ext or '(nenhuma)'}"}, status_code=415)

    fd, tmp = tempfile.mkstemp(prefix="rssp-up-", suffix=ext)
    size, h = 0, hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await up.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    return JSONResponse({"error": f"arquivo excede {s.max_upload_mb} MB"}, status_code=413)
                h.update(chunk)
                out.write(chunk)
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        ctx = Ctx(request, fields)
        return await run(ctx, lambda c: _store_upload(c, tmp, ext, size, h.hexdigest(), up.filename or ""))
    finally:
        await form.close()
        if os.path.exists(tmp):
            os.unlink(tmp)


def _store_upload(ctx: Ctx, tmp: str, ext: str, size: int, sha: str, filename: str) -> dict:
    st = ctx.station("library.upload")
    db: Database = ctx.db
    dup = db.fetchone("SELECT id, title FROM media WHERE station_id = ? AND sha256 = ?", (st["id"], sha))
    if dup:
        raise HTTPError(409, f"arquivo idêntico já existe na biblioteca: {dup['title']}", "duplicate",
                        {"media_id": dup["id"]})
    try:
        p = probe(tmp)
    except MediaError as e:
        raise HTTPError(422, str(e), "invalid_media") from e
    kind = ctx.body.get("kind") or "music"
    if kind not in KINDS:
        raise HTTPError(422, "tipo inválido", "validation")
    mid = new_id()
    key = f"stations/{st['id']}/media/{mid}{ext}"
    get_storage().put_file(key, tmp)
    t = p.tags
    title = (ctx.body.get("title") or t.get("title") or Path(filename).stem or "Sem título")[:300]
    year = None
    for cand in (t.get("date"), t.get("year")):
        if cand and str(cand)[:4].isdigit():
            year = int(str(cand)[:4])
            break
    bpm = None
    try:
        bpm = float(t["tbpm"] if "tbpm" in t else t["bpm"]) if ("tbpm" in t or "bpm" in t) else None
    except (ValueError, TypeError):
        bpm = None
    row = {"id": mid, "station_id": st["id"], "kind": kind, "title": title,
           "artist": (ctx.body.get("artist") or t.get("artist") or t.get("album_artist") or None),
           "album": t.get("album"), "genre": t.get("genre"), "year": year, "bpm": bpm, "rating": None,
           "tags": "[]", "storage_key": key, "cover_key": None, "original_filename": filename[:255],
           "container": p.container, "size_bytes": size, "sha256": sha, "duration_ms": p.duration_ms,
           "codec": p.codec, "bitrate_kbps": p.bitrate_kbps, "sample_rate": p.sample_rate, "channels": p.channels,
           "loudness_lufs": None, "true_peak_db": None, "lead_silence_ms": None, "trail_silence_ms": None,
           "cue_in_ms": 0, "cue_out_ms": None, "fade_in_ms": 0, "fade_out_ms": 0, "analysis_status": "pending",
           "analysis_error": None, "license_status": "unknown", "rights_holder": None, "license_ref": None,
           "license_expires_at": None, "isrc": t.get("isrc"), "is_demo": 1 if ctx.body.get("is_demo") == "1" else 0,
           "is_active": 1, "created_by": ctx.user["id"], "created_at": now_iso(), "updated_at": now_iso()}
    try:
        db.insert("media", row)
    except Exception:
        get_storage().delete(key)
        raise
    enqueue(db, "analyze_media", {"media_id": mid, "station_id": st["id"]}, st["id"])
    ctx.audit("media.uploaded", st["id"], "media", mid, {"title": title, "size": size})
    return media_out(row)
