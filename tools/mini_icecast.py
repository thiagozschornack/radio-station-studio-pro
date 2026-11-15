#!/usr/bin/env python3
"""MINI ICECAST — servidor de TESTE/DESENVOLVIMENTO. NÃO USE EM PRODUÇÃO.

Implementa o subconjunto do protocolo Icecast 2.4 usado pelo RSSP, para homologar o playout
sem um Icecast instalado:
  PUT /<mount>                 origem (Basic auth, Expect: 100-continue), 403 se o mount já estiver em uso
  GET /<mount>                 ouvinte (recebe o fluxo ao vivo, com rajada inicial de ~64 KB)
  GET /status-json.xsl         status no formato icestats
  GET /admin/metadata?...      atualização de título (mode=updinfo)

Em produção use Icecast 2.4+ (ver deploy/icecast.xml) atrás de um proxy HTTPS.

  python tools/mini_icecast.py --port 8000 --password segredo123
"""
from __future__ import annotations

import argparse
import base64
import json
import socketserver
import threading
import time
import urllib.parse


class State:
    def __init__(self, password: str, user: str = "source"):
        self.user, self.password = user, password
        self.lock = threading.Lock()
        self.mounts: dict[str, dict] = {}

    def auth_ok(self, header: str | None) -> bool:
        if not header or not header.lower().startswith("basic "):
            return False
        try:
            u, p = base64.b64decode(header[6:]).decode().split(":", 1)
        except Exception:
            return False
        return u == self.user and p == self.password


class Handler(socketserver.StreamRequestHandler):
    state: State
    port: int

    def _headers(self) -> tuple[str, str, dict]:
        line = self.rfile.readline(8192).decode("latin-1").strip()
        parts = line.split(" ")
        method, path = (parts[0], parts[1]) if len(parts) >= 2 else ("", "/")
        headers = {}
        while True:
            h = self.rfile.readline(8192).decode("latin-1")
            if h in ("\r\n", "\n", ""):
                break
            k, _, v = h.partition(":")
            headers[k.strip().lower()] = v.strip()
        return method, path, headers

    def _reply(self, code: int, text: str, body: bytes = b"", ctype: str = "text/plain") -> None:
        self.wfile.write(f"HTTP/1.1 {code} {text}\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                         f"Connection: close\r\n\r\n".encode() + body)

    def handle(self) -> None:
        method, path, headers = self._headers()
        url = urllib.parse.urlparse(path)
        if method in ("PUT", "SOURCE"):
            return self._source(url.path, headers)
        if method == "GET" and url.path == "/status-json.xsl":
            return self._status()
        if method == "GET" and url.path == "/admin/metadata":
            return self._metadata(urllib.parse.parse_qs(url.query), headers)
        if method == "GET":
            return self._listener(url.path)
        self._reply(405, "Method Not Allowed")

    def _source(self, mount: str, headers: dict) -> None:
        st = self.state
        if not st.auth_ok(headers.get("authorization")):
            return self._reply(401, "Authentication Required")
        with st.lock:
            m = st.mounts.get(mount)
            if m and m["source_connected"]:
                return self._reply(403, "Mountpoint in use")
            m = m or {"listeners": [], "burst": bytearray(), "title": "", "bytes_in": 0}
            m.update(source_connected=True, content_type=headers.get("content-type", "audio/mpeg"),
                     name=headers.get("ice-name", ""), started=time.time(), sock=self.connection, burst=bytearray())
            st.mounts[mount] = m
        if "100-continue" in headers.get("expect", "").lower():
            self.wfile.write(b"HTTP/1.1 100 Continue\r\n\r\n")
        else:
            self.wfile.write(b"HTTP/1.1 200 OK\r\n\r\n")
        self.wfile.flush()
        try:
            while True:
                data = self.rfile.read1(16384) if hasattr(self.rfile, "read1") else self.rfile.read(4096)
                if not data:
                    break
                with st.lock:
                    m["bytes_in"] += len(data)
                    m["burst"] += data
                    if len(m["burst"]) > 65536:
                        del m["burst"][:len(m["burst"]) - 65536]
                    dead = []
                    for lw in m["listeners"]:
                        try:
                            lw.write(data)
                        except OSError:
                            dead.append(lw)
                    for d in dead:
                        m["listeners"].remove(d)
        finally:
            with st.lock:
                m["source_connected"] = False

    def _listener(self, mount: str) -> None:
        st = self.state
        with st.lock:
            m = st.mounts.get(mount)
            if not m or not m["source_connected"]:
                return self._reply(404, "Not Found")
            self.wfile.write(f"HTTP/1.0 200 OK\r\nContent-Type: {m['content_type']}\r\nicy-name: {m['name']}\r\n"
                             f"Cache-Control: no-cache\r\nAccess-Control-Allow-Origin: *\r\n\r\n".encode())
            self.wfile.write(bytes(m["burst"]))
            m["listeners"].append(self.wfile)
        try:
            while True:
                time.sleep(0.5)
                with st.lock:
                    if self.wfile not in m["listeners"]:
                        break
        finally:
            with st.lock:
                if self.wfile in m["listeners"]:
                    m["listeners"].remove(self.wfile)

    def _status(self) -> None:
        st = self.state
        with st.lock:
            src = [{"listenurl": f"http://127.0.0.1:{self.port}{k}", "listeners": len(v["listeners"]),
                    "server_name": v["name"], "title": v["title"], "server_type": v["content_type"],
                    "total_bytes_read": v["bytes_in"]} for k, v in st.mounts.items() if v["source_connected"]]
        body = json.dumps({"icestats": {"admin": "dev", "server_id": "mini-icecast (TESTE)",
                                        "source": src[0] if len(src) == 1 else (src or None)}}).encode()
        self._reply(200, "OK", body, "application/json")

    def _metadata(self, q: dict, headers: dict) -> None:
        st = self.state
        if not st.auth_ok(headers.get("authorization")):
            return self._reply(401, "Authentication Required")
        mount = (q.get("mount") or [""])[0]
        with st.lock:
            m = st.mounts.get(mount)
            if not m:
                return self._reply(400, "Source does not exist")
            m["title"] = (q.get("song") or [""])[0]
        self._reply(200, "OK", b"<?xml version=\"1.0\"?><iceresponse><message>Metadata update successful</message>"
                               b"<return>1</return></iceresponse>", "text/xml")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def kill_all(self) -> None:
        """Teste de falha: derruba origens e ouvintes e encerra o servidor."""
        import socket as _s
        st = self.state  # type: ignore[attr-defined]
        with st.lock:
            for m in st.mounts.values():
                sock = m.get("sock")
                if sock is not None:
                    try:
                        sock.shutdown(_s.SHUT_RDWR)
                    except OSError:
                        pass
                m["listeners"].clear()
        self.shutdown()
        self.server_close()


def serve(port: int, password: str) -> Server:
    state = State(password)
    handler = type("H", (Handler,), {"state": state, "port": port})
    srv = Server(("127.0.0.1", port), handler)
    srv.state = state  # type: ignore[attr-defined]
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--password", required=True)
    a = ap.parse_args()
    s = serve(a.port, a.password)
    print(f"mini-icecast (TESTE) em http://127.0.0.1:{a.port}")
    s.serve_forever()
