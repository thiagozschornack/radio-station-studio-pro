"""Homologação ponta a ponta do playout: áudio real codificado, enviado por protocolo Icecast,
recebido por um ouvinte externo e decodificado por FFprobe. Cobre log de execução, pulo,
queda do servidor, reinício do serviço (kill -9) e instância única por emissora."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests

from conftest import BACKEND, Client, free_port, make_audio, make_user, new_station, run_jobs, upload
from rssp.db import get_db

import mini_icecast

ICE_PW = "IceSecret-" + uuid.uuid4().hex[:6]


def wait_for(cond, timeout=20.0, step=0.2, msg="condição"):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = cond()
        if last:
            return last
        time.sleep(step)
    raise AssertionError(f"tempo esgotado aguardando {msg} (último: {last})")


class Ice:
    def __init__(self, port=None):
        self.port = port or free_port()
        self.start()

    def start(self):
        self.srv = mini_icecast.serve(self.port, ICE_PW)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def kill(self):
        self.srv.kill_all()

    def status(self):
        try:
            return requests.get(f"http://127.0.0.1:{self.port}/status-json.xsl", timeout=2).json()["icestats"]["source"]
        except Exception:
            return None

    def listen(self, mount, seconds):
        r = requests.get(f"http://127.0.0.1:{self.port}{mount}", stream=True, timeout=10)
        assert r.status_code == 200
        buf, t = b"", time.time()
        for chunk in r.iter_content(4096):
            buf += chunk
            if time.time() - t > seconds:
                break
        r.close()
        return r.headers.get("content-type"), buf


def start_engine(sid):
    env = dict(os.environ, PYTHONPATH=str(BACKEND))
    return subprocess.Popen([sys.executable, "-m", "rssp.playout", "--station", sid], cwd=BACKEND, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def stop_engine(p, sig=signal.SIGTERM):
    if p.poll() is None:
        p.send_signal(sig)
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
    return p.stdout.read().decode(errors="replace")


def ffprobe_bytes(data: bytes, tmp: Path, ext: str) -> dict:
    f = tmp / f"cap-{uuid.uuid4().hex[:6]}.{ext}"
    f.write_bytes(data)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate,channels",
                          "-show_entries", "format=duration", "-of", "json", str(f)], capture_output=True)
    assert out.returncode == 0, out.stderr
    info = json.loads(out.stdout)
    # decodificação completa sem erros
    dec = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "null", "-"], capture_output=True)
    info["decode_errors"] = dec.stderr.decode(errors="replace").strip()
    return info


def plays(sid):
    return get_db().fetchall("SELECT * FROM play_log WHERE station_id = ? ORDER BY started_at", (sid,))


def station_setup(base_url, admin, tmp, ice, fmt="mp3", tracks=3, seconds=4, rotation=False):
    u = make_user()
    st = new_station(admin, u["email"])
    c = Client(base_url, u["email"])
    sid = st["id"]
    ids = []
    for i in range(tracks):
        m = upload(c, sid, make_audio(tmp / f"t{i}-{uuid.uuid4().hex[:4]}.mp3", seconds, 300 + 150 * i,
                                      title=f"Faixa {i}", artist="Teste")).json()
        ids.append(m["id"])
    run_jobs()
    r = c.put(f"/api/stations/{sid}/stream", {"host": "127.0.0.1", "port": ice.port, "mount": f"/{st['slug']}",
                                              "source_password": ICE_PW, "format": fmt, "bitrate_kbps": 128,
                                              "rotation_fallback": rotation})
    assert r.status_code == 200, r.text
    return c, st, ids


def test_e2e_mp3_full_cycle(base_url, admin, tmpdir_path):
    ice = Ice()
    c, st, ids = station_setup(base_url, admin, tmpdir_path, ice)
    sid, mount = st["id"], f"/{st['slug']}"
    c.post(f"/api/stations/{sid}/queue", {"media_ids": ids + [ids[0]]})
    c.post(f"/api/stations/{sid}/stream/start")
    eng = start_engine(sid)
    try:
        wait_for(lambda: c.get(f"/api/stations/{sid}/status").json()["status"] == "on_air", msg="no ar")
        # --- ouvinte externo recebe áudio MP3 real e decodificável
        ctype, data = ice.listen(mount, 3)
        assert ctype == "audio/mpeg" and len(data) > 30_000
        info = ffprobe_bytes(data, tmpdir_path, "mp3")
        assert info["streams"][0]["codec_name"] == "mp3" and info["streams"][0]["channels"] == 2
        assert float(info["format"]["duration"]) > 2.0
        # --- metadados chegaram ao servidor
        wait_for(lambda: (ice.status() or {}).get("title", "").startswith("Teste - Faixa"), msg="metadados")
        # --- status do painel e do endpoint público coerentes com o playout real
        s = c.get(f"/api/stations/{sid}/status").json()
        assert s["now_playing"]["title"].startswith("Faixa") and s["encoder_connected"]
        assert requests.get(f"{base_url}/api/public/stations/{st['slug']}").json()["on_air"] is True
        # --- pular
        wait_for(lambda: any(p["title"] == "Faixa 1" and p["ended_at"] is None for p in plays(sid)), msg="faixa 1 no ar")
        c.post(f"/api/stations/{sid}/playout/skip")
        wait_for(lambda: any(p["title"] == "Faixa 1" and p["end_reason"] == "skipped" for p in plays(sid)), msg="pulo")
        # --- instância única
        dup = start_engine(sid)
        assert dup.wait(timeout=15) != 0
        assert "outra instância" in dup.stdout.read().decode()
        # --- queda do serviço (kill -9) durante a Faixa 2 e recuperação
        wait_for(lambda: any(p["title"] == "Faixa 2" and p["ended_at"] is None for p in plays(sid)), msg="faixa 2")
        time.sleep(1.0)
        stop_engine(eng, signal.SIGKILL)
        time.sleep(1.5)
        eng = start_engine(sid)
        wait_for(lambda: any(p["title"] == "Faixa 2" and p["end_reason"] == "interrupted" for p in plays(sid)),
                 msg="execução interrompida registrada")
        wait_for(lambda: any(p["title"] == "Faixa 2" and p["end_reason"] == "completed" for p in plays(sid)),
                 timeout=30, msg="faixa 2 retomada e concluída")
        # --- queda do servidor de streaming: não contabiliza execução sem ouvintes possíveis
        wait_for(lambda: any(p["title"] == "Faixa 0" and p["ended_at"] is None and p["source"] == "manual"
                             and p["started_at"] > plays(sid)[0]["started_at"] for p in plays(sid)), msg="faixa 0 repetida")
        ice.kill()
        t_out = time.time()
        wait_for(lambda: c.get(f"/api/stations/{sid}/status").json()["encoder_connected"] is False, msg="queda detectada")
        time.sleep(2.5)
        outage = time.time() - t_out
        ice = Ice(ice.port)
        wait_for(lambda: c.get(f"/api/stations/{sid}/status").json()["status"] in ("on_air", "dead_air")
                 and c.get(f"/api/stations/{sid}/status").json()["encoder_connected"], timeout=40, msg="reconexão")
        last0 = wait_for(lambda: next((p for p in plays(sid) if p["title"] == "Faixa 0" and p["end_reason"] == "completed"
                                       and p["started_at"] > plays(sid)[0]["started_at"]), None), timeout=30,
                         msg="faixa 0 concluída após reconexão")
        wall = (time.mktime(time.strptime(last0["ended_at"][:19], "%Y-%m-%dT%H:%M:%S")) -
                time.mktime(time.strptime(last0["started_at"][:19], "%Y-%m-%dT%H:%M:%S")))
        assert last0["played_ms"] <= last0["planned_ms"] + 300          # tempo realmente transmitido
        assert wall >= outage - 1.5                                     # o relógio passou pela queda
        assert c.get(f"/api/stations/{sid}/status").json()["reconnects"] >= 1
    finally:
        log = stop_engine(eng)
        ice.kill()
    # --- desligamento limpo
    s = c.get(f"/api/stations/{sid}/status").json()
    assert s["status"] == "offline", log
    csv = c.get(f"/api/stations/{sid}/plays.csv").text
    assert "Faixa 1" in csv and "skipped" in csv and "interrupted" in csv
    assert all(p["ended_at"] for p in plays(sid))


@pytest.mark.parametrize("fmt,codec,ctype,ext", [("aac", "aac", "audio/aac", "aac"),
                                                 ("opus", "opus", "audio/ogg", "ogg")])
def test_e2e_other_formats(base_url, admin, tmpdir_path, fmt, codec, ctype, ext):
    ice = Ice()
    c, st, ids = station_setup(base_url, admin, tmpdir_path, ice, fmt=fmt, tracks=1, seconds=8)
    sid = st["id"]
    c.post(f"/api/stations/{sid}/queue", {"media_id": ids[0]})
    c.post(f"/api/stations/{sid}/stream/start")
    eng = start_engine(sid)
    try:
        wait_for(lambda: c.get(f"/api/stations/{sid}/status").json()["status"] == "on_air", msg="no ar")
        got_ct, data = ice.listen(f"/{st['slug']}", 3)
        assert got_ct == ctype
        info = ffprobe_bytes(data, tmpdir_path, ext)
        assert info["streams"][0]["codec_name"] == codec
        if fmt == "opus":
            assert info["decode_errors"] == ""  # fluxo Ogg começa com cabeçalhos válidos
    finally:
        stop_engine(eng)
        ice.kill()


def test_e2e_rotation_fallback_and_stop(base_url, admin, tmpdir_path):
    ice = Ice()
    c, st, ids = station_setup(base_url, admin, tmpdir_path, ice, tracks=3, seconds=2, rotation=True)
    sid = st["id"]
    c.patch(f"/api/stations/{sid}/media/{ids[2]}", {"license_status": "blocked"})
    c.post(f"/api/stations/{sid}/stream/start")
    eng = start_engine(sid)
    try:
        wait_for(lambda: len([p for p in plays(sid) if p["end_reason"] == "completed"]) >= 4, timeout=30,
                 msg="rotação de contingência")
        ps = plays(sid)
        assert all(p["source"] == "rotation" for p in ps)
        assert all(p["media_id"] != ids[2] for p in ps)  # bloqueada nunca vai ao ar
        done = [p["media_id"] for p in ps if p["end_reason"]]
        assert all(a != b for a, b in zip(done, done[1:]))  # não repete em sequência
        # parar transmissão pelo painel
        c.post(f"/api/stations/{sid}/stream/stop")
        wait_for(lambda: c.get(f"/api/stations/{sid}/status").json()["status"] == "stopped", msg="parado")
        wait_for(lambda: ice.status() is None, msg="mount liberado")
        wait_for(lambda: any(p["end_reason"] == "stopped" for p in plays(sid)), timeout=5, msg="registro de parada")
    finally:
        stop_engine(eng)
        ice.kill()
