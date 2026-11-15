"""Autenticação: login, MFA (TOTP), refresh com rotação, logout, perfil."""
from __future__ import annotations

from ..config import get_settings
from ..db import new_id, now_iso
from ..rbac import ROLE_LABELS, permissions_for
from ..security import (create_access_token, create_mfa_challenge, decode_token, decrypt, encrypt,
                        hash_password, new_refresh_token, new_totp_secret, password_problems, rate_limiter,
                        sha256_hex, totp_uri, verify_password, verify_totp)
from .common import Ctx, HTTPError, endpoint

REFRESH_COOKIE = "rssp_refresh"
MAX_FAILED = 5
LOCK_MINUTES = 15
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def public_user(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"], "is_superadmin": bool(u["is_superadmin"]),
            "mfa_enabled": bool(u["mfa_enabled"])}


def _issue_session(ctx: Ctx, user: dict, mfa: bool) -> dict:
    s = get_settings()
    raw, digest = new_refresh_token()
    ctx.db.insert("refresh_tokens", {"id": new_id(), "user_id": user["id"], "token_hash": digest,
                                     "expires_at": now_iso(s.refresh_token_days * 86400), "revoked_at": None,
                                     "user_agent": (ctx.request.headers.get("user-agent") or "")[:200],
                                     "ip": ctx.ip, "created_at": now_iso()})
    ctx.cookies_to_set.append((REFRESH_COOKIE, raw, {
        "httponly": True, "secure": s.cookie_secure, "samesite": "strict", "path": "/api/auth",
        "max_age": s.refresh_token_days * 86400}))
    return {"access_token": create_access_token(user["id"], mfa), "token_type": "bearer",
            "expires_in": s.access_token_minutes * 60, "user": public_user(user)}


@endpoint
def login(ctx: Ctx):
    s = get_settings()
    if not rate_limiter.allow("login:" + ctx.ip, s.login_rate_per_minute):
        raise HTTPError(429, "muitas tentativas; aguarde um minuto", "rate_limited")
    email = ctx.field("email").lower()
    password = ctx.field("password")
    u = ctx.db.fetchone("SELECT * FROM users WHERE email = ?", (email,))
    if not u:
        verify_password(password, _DUMMY_HASH)  # tempo constante aproximado
        raise HTTPError(401, "e-mail ou senha inválidos", "bad_credentials")
    if u["locked_until"] and u["locked_until"] > now_iso():
        raise HTTPError(423, "conta temporariamente bloqueada por tentativas inválidas", "locked")
    if not u["is_active"] or not verify_password(password, u["password_hash"]):
        fails = u["failed_logins"] + 1
        vals = {"failed_logins": fails}
        if fails >= MAX_FAILED:
            vals = {"failed_logins": 0, "locked_until": now_iso(LOCK_MINUTES * 60)}
        ctx.db.update("users", vals, "id = ?", (u["id"],))
        ctx.audit("auth.login_failed", user_id=u["id"])
        raise HTTPError(401, "e-mail ou senha inválidos", "bad_credentials")
    ctx.db.update("users", {"failed_logins": 0, "locked_until": None}, "id = ?", (u["id"],))
    if u["mfa_enabled"]:
        return {"mfa_required": True, "challenge": create_mfa_challenge(u["id"])}
    ctx.audit("auth.login", user_id=u["id"])
    return _issue_session(ctx, u, mfa=False)


@endpoint
def login_mfa(ctx: Ctx):
    if not rate_limiter.allow("mfa:" + ctx.ip, get_settings().login_rate_per_minute):
        raise HTTPError(429, "muitas tentativas; aguarde um minuto", "rate_limited")
    data = decode_token(ctx.field("challenge"), "mfa")
    if not data:
        raise HTTPError(401, "desafio MFA expirado; faça login novamente", "mfa_expired")
    u = ctx.db.fetchone("SELECT * FROM users WHERE id = ? AND is_active = 1", (data["sub"],))
    secret = decrypt(u["mfa_secret_enc"]) if u else None
    if not u or not secret or not verify_totp(secret, ctx.field("code")):
        raise HTTPError(401, "código MFA inválido", "bad_mfa")
    ctx.audit("auth.login", user_id=u["id"], details={"mfa": True})
    return _issue_session(ctx, u, mfa=True)


@endpoint
def refresh(ctx: Ctx):
    raw = ctx.request.cookies.get(REFRESH_COOKIE)
    if not raw:
        raise HTTPError(401, "sem sessão", "unauthenticated")
    row = ctx.db.fetchone("SELECT * FROM refresh_tokens WHERE token_hash = ?", (sha256_hex(raw),))
    if not row or row["expires_at"] < now_iso():
        raise HTTPError(401, "sessão expirada", "unauthenticated")
    if row["revoked_at"]:
        # Reuso de token já rotacionado: possível roubo. Revoga todas as sessões do usuário.
        ctx.db.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                       (now_iso(), row["user_id"]))
        ctx.audit("auth.refresh_reuse", user_id=row["user_id"])
        raise HTTPError(401, "sessão inválida", "unauthenticated")
    u = ctx.db.fetchone("SELECT * FROM users WHERE id = ? AND is_active = 1", (row["user_id"],))
    if not u:
        raise HTTPError(401, "usuário inativo", "unauthenticated")
    ctx.db.update("refresh_tokens", {"revoked_at": now_iso()}, "id = ?", (row["id"],))
    return _issue_session(ctx, u, mfa=bool(u["mfa_enabled"]))


@endpoint
def logout(ctx: Ctx):
    raw = ctx.request.cookies.get(REFRESH_COOKIE)
    if raw:
        ctx.db.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                       (now_iso(), sha256_hex(raw)))
    ctx.cookies_to_set.append((REFRESH_COOKIE, "", {"path": "/api/auth", "max_age": 0, "httponly": True,
                                                    "samesite": "strict"}))
    return {"ok": True}


@endpoint
def me(ctx: Ctx):
    u = ctx.user
    if u["is_superadmin"]:
        rows = ctx.db.fetchall("SELECT s.*, m.role FROM stations s LEFT JOIN station_members m "
                               "ON m.station_id = s.id AND m.user_id = ? ORDER BY s.name", (u["id"],))
    else:
        rows = ctx.db.fetchall("SELECT s.*, m.role FROM stations s JOIN station_members m ON m.station_id = s.id "
                               "WHERE m.user_id = ? ORDER BY s.name", (u["id"],))
    stations = [{"id": r["id"], "slug": r["slug"], "name": r["name"], "role": r["role"],
                 "role_label": ROLE_LABELS.get(r["role"] or ("superadmin" if u["is_superadmin"] else ""), ""),
                 "permissions": permissions_for(r["role"], bool(u["is_superadmin"]))} for r in rows]
    return {"user": public_user(u), "stations": stations}


@endpoint
def change_password(ctx: Ctx):
    u = ctx.user
    if not verify_password(ctx.field("current_password"), u["password_hash"]):
        raise HTTPError(401, "senha atual incorreta", "bad_credentials")
    new = ctx.field("new_password")
    probs = password_problems(new)
    if probs:
        raise HTTPError(422, "senha fraca: " + "; ".join(probs), "weak_password")
    ctx.db.update("users", {"password_hash": hash_password(new), "updated_at": now_iso()}, "id = ?", (u["id"],))
    ctx.db.execute("UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                   (now_iso(), u["id"]))
    ctx.audit("auth.password_changed")
    return {"ok": True}


@endpoint
def mfa_setup(ctx: Ctx):
    u = ctx.user
    if u["mfa_enabled"]:
        raise HTTPError(409, "MFA já está ativo")
    secret = new_totp_secret()
    ctx.db.update("users", {"mfa_secret_enc": encrypt(secret), "updated_at": now_iso()}, "id = ?", (u["id"],))
    return {"secret": secret, "otpauth_uri": totp_uri(secret, u["email"])}


@endpoint
def mfa_enable(ctx: Ctx):
    u = ctx.user
    secret = decrypt(u["mfa_secret_enc"])
    if not secret:
        raise HTTPError(409, "inicie a configuração do MFA primeiro")
    if not verify_totp(secret, ctx.field("code")):
        raise HTTPError(422, "código inválido", "bad_mfa")
    ctx.db.update("users", {"mfa_enabled": 1, "updated_at": now_iso()}, "id = ?", (u["id"],))
    ctx.audit("auth.mfa_enabled")
    return {"ok": True}


@endpoint
def mfa_disable(ctx: Ctx):
    u = ctx.user
    secret = decrypt(u["mfa_secret_enc"])
    if not verify_password(ctx.field("password"), u["password_hash"]) or not secret \
            or not verify_totp(secret, ctx.field("code")):
        raise HTTPError(401, "senha ou código inválidos", "bad_credentials")
    ctx.db.update("users", {"mfa_enabled": 0, "mfa_secret_enc": None, "updated_at": now_iso()}, "id = ?", (u["id"],))
    ctx.audit("auth.mfa_disabled")
    return {"ok": True}

