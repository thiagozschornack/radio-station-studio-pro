"""Emissoras, membros (RBAC), usuários (superadmin) e configuração de transmissão."""
from __future__ import annotations

import re
import secrets

from ..db import new_id, now_iso
from ..rbac import ROLE_LABELS, ROLES
from ..security import encrypt, hash_password, password_problems
from .common import Ctx, HTTPError, endpoint

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
MOUNT_RE = re.compile(r"^/[A-Za-z0-9._-]{1,64}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FORMATS = {"mp3": "audio/mpeg", "aac": "audio/aac", "opus": "audio/ogg"}
BITRATES = [32, 48, 64, 96, 128, 160, 192, 256, 320]


def station_out(s: dict) -> dict:
    return {k: s[k] for k in ("id", "slug", "name", "slogan", "description", "timezone", "primary_color",
                              "plan", "is_active", "created_at")} | {"show_history": bool(s["show_history"])}


# ------------------------------------------------------------------ usuários (superadmin)
@endpoint
def list_users(ctx: Ctx):
    ctx.require_superadmin()
    rows = ctx.db.fetchall("SELECT id, email, name, is_superadmin, is_active, mfa_enabled, created_at FROM users "
                           "ORDER BY created_at")
    return {"users": rows}


def create_user_row(ctx: Ctx, email: str, name: str, password: str | None, superadmin: bool = False) -> tuple[dict, str | None]:
    email = email.lower()
    if not EMAIL_RE.match(email):
        raise HTTPError(422, "e-mail inválido", "validation")
    if ctx.db.fetchone("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPError(409, "já existe usuário com este e-mail", "conflict")
    temp = None
    if not password:
        temp = secrets.token_urlsafe(9) + "Aa1"
        password = temp
    probs = password_problems(password)
    if probs:
        raise HTTPError(422, "senha fraca: " + "; ".join(probs), "weak_password")
    u = {"id": new_id(), "email": email, "name": name or email.split("@")[0], "password_hash": hash_password(password),
         "is_superadmin": 1 if superadmin else 0, "is_active": 1, "mfa_secret_enc": None, "mfa_enabled": 0,
         "failed_logins": 0, "locked_until": None, "created_at": now_iso(), "updated_at": now_iso()}
    ctx.db.insert("users", u)
    return u, temp


@endpoint
def create_user(ctx: Ctx):
    ctx.require_superadmin()
    u, temp = create_user_row(ctx, ctx.field("email"), ctx.field("name", False, ""), ctx.field("password", False),
                              bool(ctx.field("is_superadmin", False, False, bool)))
    ctx.audit("user.created", target_type="user", target_id=u["id"], details={"email": u["email"]})
    return {"id": u["id"], "email": u["email"], "temporary_password": temp}


# ------------------------------------------------------------------ emissoras
@endpoint
def create_station(ctx: Ctx):
    ctx.require_superadmin()
    slug = ctx.field("slug").lower()
    if not SLUG_RE.match(slug):
        raise HTTPError(422, "slug deve ter 3–41 caracteres: letras minúsculas, números e hífen", "validation")
    if ctx.db.fetchone("SELECT id FROM stations WHERE slug = ?", (slug,)):
        raise HTTPError(409, "slug já utilizado", "conflict")
    sid = new_id()
    now = now_iso()
    with ctx.db.transaction():
        ctx.db.insert("stations", {"id": sid, "slug": slug, "name": ctx.field("name"),
                                   "slogan": ctx.field("slogan", False, ""), "description": ctx.field("description", False, ""),
                                   "timezone": ctx.field("timezone", False, "America/Sao_Paulo"),
                                   "primary_color": "#1f8bff", "show_history": 1, "plan": "demo", "is_active": 1,
                                   "created_at": now, "updated_at": now})
        ctx.db.insert("stream_configs", {"station_id": sid, "mount": f"/{slug}", "stream_name": ctx.field("name"),
                                         "updated_at": now})
        ctx.db.insert("playout_state", {"station_id": sid, "status": "offline", "updated_at": now})
        owner_email = ctx.field("owner_email", False)
        owner_id = ctx.user["id"]
        if owner_email:
            o = ctx.db.fetchone("SELECT id FROM users WHERE email = ?", (owner_email.lower(),))
            if not o:
                raise HTTPError(404, "proprietário não encontrado", "not_found")
            owner_id = o["id"]
        ctx.db.insert("station_members", {"station_id": sid, "user_id": owner_id, "role": "owner", "created_at": now})
        ctx.audit("station.created", sid, "station", sid, {"slug": slug})
    return station_out(ctx.db.fetchone("SELECT * FROM stations WHERE id = ?", (sid,)))


@endpoint
def get_station(ctx: Ctx):
    st = ctx.station("station.view")
    return station_out(st) | {"my_role": ctx.role_in(st["id"]) or ("superadmin" if ctx.user["is_superadmin"] else None)}


@endpoint
def update_station(ctx: Ctx):
    st = ctx.station("station.edit")
    vals = {}
    for f in ("name", "slogan", "description", "timezone"):
        if f in ctx.body:
            vals[f] = ctx.field(f, False, "")
    if "primary_color" in ctx.body:
        c = ctx.field("primary_color")
        if not COLOR_RE.match(c):
            raise HTTPError(422, "cor inválida (#RRGGBB)", "validation")
        vals["primary_color"] = c
    if "show_history" in ctx.body:
        vals["show_history"] = 1 if ctx.field("show_history", typ=bool) else 0
    if "timezone" in vals:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(vals["timezone"])
        except Exception as e:
            raise HTTPError(422, "fuso horário inválido", "validation") from e
    if vals:
        vals["updated_at"] = now_iso()
        ctx.db.update("stations", vals, "id = ?", (st["id"],))
        ctx.audit("station.updated", st["id"], "station", st["id"], {"fields": sorted(vals)})
    return station_out(ctx.db.fetchone("SELECT * FROM stations WHERE id = ?", (st["id"],)))


# ------------------------------------------------------------------ membros
@endpoint
def list_members(ctx: Ctx):
    st = ctx.station("station.view")
    rows = ctx.db.fetchall("SELECT u.id, u.email, u.name, m.role, m.created_at FROM station_members m "
                           "JOIN users u ON u.id = m.user_id WHERE m.station_id = ? ORDER BY u.name", (st["id"],))
    for r in rows:
        r["role_label"] = ROLE_LABELS.get(r["role"], r["role"])
    return {"members": rows, "roles": [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES]}


@endpoint
def add_member(ctx: Ctx):
    st = ctx.station("members.manage")
    role = ctx.field("role")
    if role not in ROLES:
        raise HTTPError(422, "papel inválido", "validation")
    email = ctx.field("email").lower()
    u = ctx.db.fetchone("SELECT id FROM users WHERE email = ?", (email,))
    temp = None
    if not u:
        created, temp = create_user_row(ctx, email, ctx.field("name", False, ""), None)
        u = {"id": created["id"]}
    if ctx.db.fetchone("SELECT 1 AS x FROM station_members WHERE station_id = ? AND user_id = ?", (st["id"], u["id"])):
        ctx.db.update("station_members", {"role": role}, "station_id = ? AND user_id = ?", (st["id"], u["id"]))
    else:
        ctx.db.insert("station_members", {"station_id": st["id"], "user_id": u["id"], "role": role, "created_at": now_iso()})
    ctx.audit("member.set", st["id"], "user", u["id"], {"role": role})
    return {"user_id": u["id"], "role": role, "temporary_password": temp}


@endpoint
def remove_member(ctx: Ctx):
    st = ctx.station("members.manage")
    uid = ctx.params["uid"]
    owners = ctx.db.scalar("SELECT COUNT(*) AS c FROM station_members WHERE station_id = ? AND role = 'owner'", (st["id"],))
    row = ctx.db.fetchone("SELECT role FROM station_members WHERE station_id = ? AND user_id = ?", (st["id"], uid))
    if not row:
        raise HTTPError(404, "membro não encontrado")
    if row["role"] == "owner" and owners <= 1:
        raise HTTPError(409, "a emissora precisa de ao menos um proprietário")
    ctx.db.execute("DELETE FROM station_members WHERE station_id = ? AND user_id = ?", (st["id"], uid))
    ctx.audit("member.removed", st["id"], "user", uid)
    return {"ok": True}


# ------------------------------------------------------------------ transmissão
def stream_out(c: dict) -> dict:
    out = {k: c[k] for k in ("server_type", "host", "port", "mount", "source_user", "format", "bitrate_kbps",
                             "sample_rate", "channels", "public_listen_url", "status_url", "stream_name",
                             "stream_genre", "stream_description", "fallback_media_id", "target_lufs",
                             "no_repeat_window", "updated_at")}
    out.update({"use_tls": bool(c["use_tls"]), "enabled": bool(c["enabled"]), "normalize": bool(c["normalize"]),
                "rotation_fallback": bool(c["rotation_fallback"]),
                # A senha nunca sai do servidor: apenas indicamos se existe.
                "has_source_password": bool(c["source_password_enc"]),
                "content_type": FORMATS.get(c["format"])})
    return out


@endpoint
def get_stream(ctx: Ctx):
    st = ctx.station("stream.view")
    c = ctx.db.fetchone("SELECT * FROM stream_configs WHERE station_id = ?", (st["id"],))
    return stream_out(c) | {"formats": list(FORMATS), "bitrates": BITRATES}


@endpoint
def update_stream(ctx: Ctx):
    st = ctx.station("stream.edit")
    b = ctx.body
    vals: dict = {}
    if "host" in b:
        h = ctx.field("host")
        if not HOST_RE.match(h):
            raise HTTPError(422, "host inválido", "validation")
        vals["host"] = h
    if "port" in b:
        p = ctx.field("port", typ=int)
        if not 1 <= p <= 65535:
            raise HTTPError(422, "porta inválida", "validation")
        vals["port"] = p
    if "mount" in b:
        m = ctx.field("mount")
        if not MOUNT_RE.match(m):
            raise HTTPError(422, "mountpoint inválido (ex.: /minha-radio)", "validation")
        vals["mount"] = m
    if "source_user" in b:
        vals["source_user"] = ctx.field("source_user")[:64] or "source"
    if b.get("source_password"):
        pw = ctx.field("source_password")
        if len(pw) < 8:
            raise HTTPError(422, "senha de source muito curta (mín. 8)", "validation")
        vals["source_password_enc"] = encrypt(pw)
    if "format" in b:
        f = ctx.field("format")
        if f not in FORMATS:
            raise HTTPError(422, "formato não suportado", "validation")
        vals["format"] = f
    if "bitrate_kbps" in b:
        br = ctx.field("bitrate_kbps", typ=int)
        if br not in BITRATES:
            raise HTTPError(422, "bitrate inválido", "validation")
        vals["bitrate_kbps"] = br
    if "sample_rate" in b:
        sr = ctx.field("sample_rate", typ=int)
        if sr not in (44100, 48000):
            raise HTTPError(422, "sample rate deve ser 44100 ou 48000", "validation")
        vals["sample_rate"] = sr
    if "channels" in b:
        ch = ctx.field("channels", typ=int)
        if ch not in (1, 2):
            raise HTTPError(422, "canais deve ser 1 ou 2", "validation")
        vals["channels"] = ch
    for f in ("public_listen_url", "status_url"):
        if f in b:
            u = ctx.field(f, False, "")
            if u and not re.match(r"^https?://[^\s\"'<>]+$", u):
                raise HTTPError(422, f"{f} deve ser uma URL http(s)", "validation")
            vals[f] = u or None
    for f in ("stream_name", "stream_genre", "stream_description"):
        if f in b:
            vals[f] = ctx.field(f, False, "")[:200]
    for f in ("use_tls", "normalize", "rotation_fallback"):
        if f in b:
            vals[f] = 1 if ctx.field(f, typ=bool) else 0
    if "target_lufs" in b:
        t = ctx.field("target_lufs", typ=float)
        if not -30 <= t <= -8:
            raise HTTPError(422, "alvo de loudness entre −30 e −8 LUFS", "validation")
        vals["target_lufs"] = t
    if "no_repeat_window" in b:
        n = ctx.field("no_repeat_window", typ=int)
        if not 0 <= n <= 500:
            raise HTTPError(422, "janela de não repetição entre 0 e 500", "validation")
        vals["no_repeat_window"] = n
    if "fallback_media_id" in b:
        mid = ctx.field("fallback_media_id", False)
        if mid and not ctx.db.fetchone("SELECT id FROM media WHERE id = ? AND station_id = ?", (mid, st["id"])):
            raise HTTPError(422, "mídia de contingência não pertence a esta emissora", "validation")
        vals["fallback_media_id"] = mid
    cur = ctx.db.fetchone("SELECT * FROM stream_configs WHERE station_id = ?", (st["id"],))
    fmt = vals.get("format", cur["format"])
    if fmt == "opus":
        vals["sample_rate"] = 48000  # Opus opera nativamente a 48 kHz
    if vals:
        vals["updated_at"] = now_iso()
        ctx.db.update("stream_configs", vals, "station_id = ?", (st["id"],))
        ctx.audit("stream.updated", st["id"], "stream", st["id"],
                  {"fields": sorted(k for k in vals if k != "source_password_enc") +
                   (["source_password"] if "source_password_enc" in vals else [])})
        ctx.db.insert("playout_commands", {"id": new_id(), "station_id": st["id"], "command": "reload",
                                           "payload": None, "status": "pending", "created_by": ctx.user["id"],
                                           "created_at": now_iso(), "processed_at": None})
    return stream_out(ctx.db.fetchone("SELECT * FROM stream_configs WHERE station_id = ?", (st["id"],)))


def _set_enabled(ctx: Ctx, enabled: bool):
    st = ctx.station("stream.control")
    c = ctx.db.fetchone("SELECT * FROM stream_configs WHERE station_id = ?", (st["id"],))
    if enabled and not c["source_password_enc"]:
        raise HTTPError(409, "configure a senha de source do servidor de streaming antes de iniciar", "not_configured")
    ctx.db.update("stream_configs", {"enabled": 1 if enabled else 0, "updated_at": now_iso()}, "station_id = ?", (st["id"],))
    ctx.audit("stream.started" if enabled else "stream.stopped", st["id"], "stream", st["id"])
    return {"enabled": enabled}


@endpoint
def start_stream(ctx: Ctx):
    return _set_enabled(ctx, True)


@endpoint
def stop_stream(ctx: Ctx):
    return _set_enabled(ctx, False)
