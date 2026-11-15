"""Infraestrutura comum da API: contexto da requisição, erros, autenticação e auditoria."""
from __future__ import annotations

import json
import logging
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_settings
from ..db import Database, dumps, get_db, new_id, now_iso
from ..rbac import role_can
from ..security import decode_token

log = logging.getLogger("rssp.api")


class HTTPError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None, extra: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.code, self.extra = status, message, code, extra or {}


def client_ip(request: Request) -> str:
    if get_settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


class Ctx:
    def __init__(self, request: Request, body: Any):
        self.request = request
        self.body = body if body is not None else {}
        self.db: Database = get_db()
        self.params = request.path_params
        self.query = request.query_params
        self.ip = client_ip(request)
        self._user: dict | None = None
        self.cookies_to_set: list[tuple[str, str, dict]] = []

    # ---------------------------------------------------------------- corpo
    def field(self, name: str, required: bool = True, default: Any = None, typ: type | tuple = str) -> Any:
        if not isinstance(self.body, dict):
            raise HTTPError(400, "corpo JSON inválido")
        v = self.body.get(name, default)
        if v is None:
            if required:
                raise HTTPError(422, f"campo obrigatório: {name}", "validation")
            return default
        if typ is float and isinstance(v, int) and not isinstance(v, bool):
            v = float(v)
        if typ is int and isinstance(v, bool):
            raise HTTPError(422, f"campo inválido: {name}", "validation")
        if not isinstance(v, typ):
            raise HTTPError(422, f"campo inválido: {name}", "validation")
        if isinstance(v, str):
            v = v.strip()
        return v

    # ---------------------------------------------------------------- autenticação
    @property
    def user(self) -> dict:
        if self._user is None:
            auth = self.request.headers.get("authorization", "")
            if not auth.lower().startswith("bearer "):
                raise HTTPError(401, "autenticação necessária", "unauthenticated")
            data = decode_token(auth[7:].strip(), "access")
            if not data:
                raise HTTPError(401, "sessão expirada ou inválida", "unauthenticated")
            u = self.db.fetchone("SELECT * FROM users WHERE id = ? AND is_active = 1", (data["sub"],))
            if not u:
                raise HTTPError(401, "usuário inativo", "unauthenticated")
            self._user = u
        return self._user

    def require_superadmin(self) -> dict:
        if not self.user["is_superadmin"]:
            raise HTTPError(403, "apenas superadministradores", "forbidden")
        return self.user

    def role_in(self, station_id: str) -> str | None:
        row = self.db.fetchone("SELECT role FROM station_members WHERE station_id = ? AND user_id = ?",
                               (station_id, self.user["id"]))
        return row["role"] if row else None

    def station(self, perm: str, sid_param: str = "sid") -> dict:
        """Carrega a emissora do path e exige a permissão. Responde 404 para quem não é membro
        (não revela a existência de emissoras de outros tenants)."""
        sid = self.params[sid_param]
        st = self.db.fetchone("SELECT * FROM stations WHERE id = ?", (sid,))
        if not st:
            raise HTTPError(404, "emissora não encontrada", "not_found")
        if self.user["is_superadmin"]:
            return st
        role = self.role_in(sid)
        if role is None:
            raise HTTPError(404, "emissora não encontrada", "not_found")
        if not role_can(role, perm):
            raise HTTPError(403, "seu papel não permite esta ação", "forbidden")
        return st

    def audit(self, action: str, station_id: str | None = None, target_type: str | None = None,
              target_id: str | None = None, details: dict | None = None, user_id: str | None = None) -> None:
        uid = user_id or (self._user["id"] if self._user else None)
        self.db.insert("audit_log", {"id": new_id(), "station_id": station_id, "user_id": uid, "action": action,
                                     "target_type": target_type, "target_id": target_id,
                                     "details": dumps(details) if details else None, "ip": self.ip,
                                     "created_at": now_iso()})


def endpoint(fn: Callable[[Ctx], Any]):
    """Converte uma função síncrona (ctx) -> dict|Response em endpoint Starlette.
    O corpo JSON é lido de forma assíncrona; a lógica com banco roda no threadpool."""

    async def handler(request: Request) -> Response:
        body = None
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            raw = await request.body()
            if raw:
                if len(raw) > 1_000_000:
                    return JSONResponse({"error": "corpo muito grande"}, status_code=413)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    return JSONResponse({"error": "JSON inválido"}, status_code=400)
        ctx = Ctx(request, body)
        return await run(ctx, fn)

    handler.__name__ = fn.__name__
    return handler


async def run(ctx: Ctx, fn: Callable[[Ctx], Any]) -> Response:
    try:
        result = await run_in_threadpool(fn, ctx)
    except HTTPError as e:
        payload = {"error": e.message}
        if e.code:
            payload["code"] = e.code
        payload.update(e.extra)
        return JSONResponse(payload, status_code=e.status)
    except Exception:  # noqa: BLE001
        log.exception("erro interno em %s", ctx.request.url.path)
        return JSONResponse({"error": "erro interno"}, status_code=500)
    resp = result if isinstance(result, Response) else JSONResponse(result)
    for name, value, opts in ctx.cookies_to_set:
        resp.set_cookie(name, value, **opts)
    return resp
