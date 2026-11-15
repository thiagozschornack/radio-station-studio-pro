"""Cliente de origem (source) Icecast 2.4+ e utilitários de metadados/status.

O envio é feito pelo próprio serviço (HTTP PUT), e não pelo protocolo icecast:// do FFmpeg,
para que a senha de source nunca apareça na linha de comando de processos (visível em `ps`).
"""
from __future__ import annotations

import base64
import json
import socket
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .. import __version__


@dataclass
class SourceConfig:
    host: str
    port: int
    mount: str
    user: str
    password: str
    use_tls: bool
    content_type: str
    name: str = ""
    genre: str = ""
    description: str = ""
    url: str = ""
    bitrate: int = 128
    sample_rate: int = 44100
    channels: int = 2


class IcecastError(Exception):
    pass


def _auth(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def connect_source(cfg: SourceConfig, timeout: float = 8.0) -> socket.socket:
    """Abre a conexão de source e retorna o socket pronto para receber o fluxo codificado."""
    raw = socket.create_connection((cfg.host, cfg.port), timeout=timeout)
    sock: socket.socket = raw
    if cfg.use_tls:
        ctx = ssl.create_default_context()
        sock = ctx.wrap_socket(raw, server_hostname=cfg.host)
    audio_info = f"ice-samplerate={cfg.sample_rate};ice-bitrate={cfg.bitrate};ice-channels={cfg.channels}"
    headers = [
        f"PUT {urllib.parse.quote(cfg.mount)} HTTP/1.1",
        f"Host: {cfg.host}:{cfg.port}",
        f"Authorization: {_auth(cfg.user, cfg.password)}",
        f"User-Agent: RSSP-Playout/{__version__} (EmpreenderTHZ)",
        f"Content-Type: {cfg.content_type}",
        "Ice-Public: 0",
        f"Ice-Name: {_hdr(cfg.name)}",
        f"Ice-Description: {_hdr(cfg.description)}",
        f"Ice-Genre: {_hdr(cfg.genre)}",
        f"Ice-Url: {_hdr(cfg.url)}",
        f"Ice-Audio-Info: {audio_info}",
        "Expect: 100-continue",
        "", "",
    ]
    try:
        sock.sendall("\r\n".join(headers).encode("latin-1", errors="replace"))
        resp = b""
        while b"\r\n\r\n" not in resp and len(resp) < 8192:
            chunk = sock.recv(1024)
            if not chunk:
                break
            resp += chunk
    except OSError as e:
        sock.close()
        raise IcecastError(f"falha de rede: {e}") from e
    status_line = resp.split(b"\r\n", 1)[0].decode(errors="replace")
    parts = status_line.split(" ", 2)
    code = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    if code in (100, 200):
        sock.settimeout(10.0)
        return sock
    sock.close()
    reason = {401: "credenciais de source recusadas", 403: "mountpoint em uso ou não permitido",
              404: "mountpoint inexistente", 0: "resposta inválida do servidor"}.get(code, status_line)
    raise IcecastError(f"Icecast recusou a conexão ({code}): {reason}")


def _hdr(v: str) -> str:
    return (v or "").replace("\r", " ").replace("\n", " ")[:200]


def _base(cfg: SourceConfig) -> str:
    return f"{'https' if cfg.use_tls else 'http'}://{cfg.host}:{cfg.port}"


def update_metadata(cfg: SourceConfig, song: str, timeout: float = 5.0) -> None:
    """Atualiza o título no Icecast (válido para MP3/AAC; Ogg/Opus usa comentários in-stream)."""
    q = urllib.parse.urlencode({"mount": cfg.mount, "mode": "updinfo", "song": song, "charset": "UTF-8"})
    req = urllib.request.Request(f"{_base(cfg)}/admin/metadata?{q}",
                                 headers={"Authorization": _auth(cfg.user, cfg.password)})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL configurada pelo administrador
        r.read()


def fetch_listeners(cfg: SourceConfig, status_url: str | None, timeout: float = 5.0) -> int | None:
    """Ouvintes (conexões) no mountpoint, via status-json.xsl público do Icecast."""
    url = status_url or f"{_base(cfg)}/status-json.xsl"
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    sources = (data.get("icestats") or {}).get("source")
    if sources is None:
        return 0
    if isinstance(sources, dict):
        sources = [sources]
    for s in sources:
        lu = s.get("listenurl") or ""
        if urllib.parse.urlparse(lu).path == cfg.mount or lu.endswith(cfg.mount):
            return int(s.get("listeners") or 0)
    return 0
