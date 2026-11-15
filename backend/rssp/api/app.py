"""Aplicação HTTP (Starlette/ASGI).

Execução:  uvicorn rssp.api.app:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..config import get_settings
from ..db import get_db, now_iso
from . import auth, library, operation, public, stations
from .common import Ctx, endpoint

log = logging.getLogger("rssp.api")

CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' https: http: blob:; "
       "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
        if not request.url.path.startswith("/api/"):
            resp.headers.setdefault("Content-Security-Policy", CSP)
        elif "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"
        if get_settings().cookie_secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return resp


@endpoint
def health(ctx: Ctx):
    ok = ctx.db.scalar("SELECT 1 AS ok") == 1
    return {"status": "ok" if ok else "degraded", "version": __version__, "db": ctx.db.dialect, "time": now_iso()}


@endpoint
def meta(ctx: Ctx):
    """Mapa de módulos e seu estado real de implementação (exibido no painel)."""
    from ..status import MODULES
    return {"version": __version__, "modules": MODULES}


def build_app() -> Starlette:
    s = get_settings()
    get_db().migrate()
    web = Path(s.web_dir)
    sid = "/api/stations/{sid}"
    routes = [
        Route("/api/health", health),
        Route("/api/meta", meta),
        Route("/api/auth/login", auth.login, methods=["POST"]),
        Route("/api/auth/mfa", auth.login_mfa, methods=["POST"]),
        Route("/api/auth/refresh", auth.refresh, methods=["POST"]),
        Route("/api/auth/logout", auth.logout, methods=["POST"]),
        Route("/api/me", auth.me),
        Route("/api/me/password", auth.change_password, methods=["POST"]),
        Route("/api/me/mfa/setup", auth.mfa_setup, methods=["POST"]),
        Route("/api/me/mfa/enable", auth.mfa_enable, methods=["POST"]),
        Route("/api/me/mfa/disable", auth.mfa_disable, methods=["POST"]),
        Route("/api/admin/users", stations.list_users),
        Route("/api/admin/users", stations.create_user, methods=["POST"]),
        Route("/api/stations", stations.create_station, methods=["POST"]),
        Route(sid, stations.get_station),
        Route(sid, stations.update_station, methods=["PATCH"]),
        Route(sid + "/members", stations.list_members),
        Route(sid + "/members", stations.add_member, methods=["POST"]),
        Route(sid + "/members/{uid}", stations.remove_member, methods=["DELETE"]),
        Route(sid + "/stream", stations.get_stream),
        Route(sid + "/stream", stations.update_stream, methods=["PUT"]),
        Route(sid + "/stream/start", stations.start_stream, methods=["POST"]),
        Route(sid + "/stream/stop", stations.stop_stream, methods=["POST"]),
        Route(sid + "/status", operation.station_status),
        Route(sid + "/playout/skip", operation.skip, methods=["POST"]),
        Route(sid + "/queue", operation.get_queue),
        Route(sid + "/queue", operation.add_to_queue, methods=["POST"]),
        Route(sid + "/queue", operation.clear_queue, methods=["DELETE"]),
        Route(sid + "/queue/{qid}", operation.remove_from_queue, methods=["DELETE"]),
        Route(sid + "/queue/{qid}/move", operation.move_in_queue, methods=["POST"]),
        Route(sid + "/plays", operation.plays),
        Route(sid + "/plays.csv", operation.plays_csv),
        Route(sid + "/listeners", operation.listeners),
        Route(sid + "/audit", operation.audit),
        Route(sid + "/media", library.list_media),
        Route(sid + "/media", library.upload_media, methods=["POST"]),
        Route(sid + "/media/{mid}", library.get_media),
        Route(sid + "/media/{mid}", library.update_media, methods=["PATCH"]),
        Route(sid + "/media/{mid}", library.delete_media, methods=["DELETE"]),
        Route(sid + "/media/{mid}/reanalyze", library.reanalyze_media, methods=["POST"]),
        Route(sid + "/media/{mid}/preview-url", library.preview_url),
        Route(sid + "/media/{mid}/cover", library.media_cover),
        Route("/api/media-files/{sid}/{mid}", library.media_file),
        Route("/api/public/stations/{slug}", public.public_station),
        Route("/api/public/stations/{slug}/status", public.public_status),
        Route("/api/public/stations/{slug}/cover/{mid}", public.public_cover),
        Route("/", lambda r: RedirectResponse("/admin/")),
        Route("/r/{slug}", lambda r: FileResponse(web / "player" / "index.html")),
        Mount("/admin", StaticFiles(directory=web / "admin", html=True)),
        Mount("/player", StaticFiles(directory=web / "player")),
        Mount("/shared", StaticFiles(directory=web / "shared")),
    ]

    async def not_found(request, exc):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "rota não encontrada"}, status_code=404)
        return JSONResponse({"error": "não encontrado"}, status_code=404)

    async def method_not_allowed(request, exc):
        return JSONResponse({"error": "método não permitido"}, status_code=405)

    return Starlette(routes=routes, middleware=[Middleware(SecurityHeaders)],
                     exception_handlers={404: not_found, 405: method_not_allowed})


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = build_app()
