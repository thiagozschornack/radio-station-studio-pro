"""Infraestrutura de testes: servidor HTTP real (uvicorn) sobre banco e armazenamento temporários."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import pytest

TMP = Path(tempfile.mkdtemp(prefix="rssp-test-"))
os.environ.update({
    "RSSP_ENV": "development",
    "RSSP_DATABASE_URL": os.environ.get("RSSP_TEST_DATABASE_URL", f"sqlite:///{TMP}/test.db"),
    "RSSP_STORAGE_PATH": str(TMP / "storage"),
    "RSSP_LOGIN_RATE_PER_MINUTE": "100000",
    "RSSP_MAX_UPLOAD_MB": "20",
    "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
})
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "tools"))

import requests  # noqa: E402
import uvicorn  # noqa: E402

from rssp.db import get_db, new_id, now_iso  # noqa: E402
from rssp.security import hash_password  # noqa: E402

PASSWORD = "SenhaForte123"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture(scope="session")
def base_url():
    from rssp.api.app import app
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def make_user(email: str | None = None, superadmin: bool = False) -> dict:
    db = get_db()
    email = email or f"u{uuid.uuid4().hex[:8]}@teste.dev"
    u = {"id": new_id(), "email": email, "name": email.split("@")[0], "password_hash": hash_password(PASSWORD),
         "is_superadmin": 1 if superadmin else 0, "is_active": 1, "mfa_secret_enc": None, "mfa_enabled": 0,
         "failed_logins": 0, "locked_until": None, "created_at": now_iso(), "updated_at": now_iso()}
    db.insert("users", u)
    return u


class Client:
    def __init__(self, base: str, email: str, password: str = PASSWORD):
        self.base = base
        self.s = requests.Session()
        r = self.s.post(base + "/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.token = r.json()["access_token"]

    def h(self):
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, path, **kw):
        return self.s.get(self.base + path, headers=self.h(), **kw)

    def post(self, path, json=None, **kw):
        return self.s.post(self.base + path, json=json, headers=self.h(), **kw)

    def put(self, path, json=None):
        return self.s.put(self.base + path, json=json, headers=self.h())

    def patch(self, path, json=None):
        return self.s.patch(self.base + path, json=json, headers=self.h())

    def delete(self, path, json=None):
        return self.s.delete(self.base + path, json=json, headers=self.h())


@pytest.fixture(scope="session")
def admin(base_url):
    u = make_user(f"admin-{uuid.uuid4().hex[:6]}@teste.dev", superadmin=True)
    return Client(base_url, u["email"])


def new_station(admin: Client, owner_email: str | None = None) -> dict:
    slug = "st-" + uuid.uuid4().hex[:8]
    r = admin.post("/api/stations", {"slug": slug, "name": f"Rádio {slug}", "owner_email": owner_email})
    assert r.status_code == 200, r.text
    return r.json()


def make_audio(path: Path, seconds: float = 3.0, freq: int = 440, codec: str = "mp3", cover: bool = False,
               title: str | None = None, artist: str | None = None, lead_silence: float = 0.0) -> Path:
    src = f"sine=frequency={freq}:duration={seconds}"
    if lead_silence:
        src = f"anullsrc=r=44100:cl=mono:d={lead_silence}[s];sine=frequency={freq}:duration={seconds}[t];[s][t]concat=n=2:v=0:a=1"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src]
    if cover:
        jpg = path.with_suffix(".cover.jpg")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x1f8bff:s=64x64", "-frames:v", "1", str(jpg)], check=True)
        cmd += ["-i", str(jpg), "-map", "0:a", "-map", "1:v", "-c:v", "copy", "-disposition:v", "attached_pic",
                "-id3v2_version", "3"]
    cmd += ["-ac", "2", "-ar", "48000" if codec == "ogg" else "44100"]
    cmd += {"mp3": ["-c:a", "libmp3lame", "-b:a", "128k"], "wav": ["-c:a", "pcm_s16le"],
            "flac": ["-c:a", "flac"], "ogg": ["-c:a", "libopus"]}[codec]
    if title:
        cmd += ["-metadata", f"title={title}"]
    if artist:
        cmd += ["-metadata", f"artist={artist}"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def upload(client: Client, sid: str, path: Path, **fields) -> requests.Response:
    with open(path, "rb") as f:
        return client.s.post(f"{client.base}/api/stations/{sid}/media", headers=client.h(),
                             files={"file": (path.name, f)}, data=fields)


def run_jobs():
    from rssp.jobs import run_once
    while run_once():
        pass


@pytest.fixture
def tmpdir_path():
    d = Path(tempfile.mkdtemp(prefix="rssp-case-", dir=TMP))
    return d
