"""Biblioteca, análise offline, licenças, fila, configuração de transmissão e endpoints públicos."""
import requests

from conftest import Client, make_audio, make_user, new_station, run_jobs, upload
from rssp.db import get_db


def _owner(base_url, admin):
    u = make_user()
    st = new_station(admin, u["email"])
    return Client(base_url, u["email"]), st


def test_upload_metadata_analysis_cover(base_url, admin, tmpdir_path):
    c, st = _owner(base_url, admin)
    f = make_audio(tmpdir_path / "faixa.mp3", 4, 440, cover=True, title="Canção Teste", artist="Artista X",
                   lead_silence=1.0)
    r = upload(c, st["id"], f)
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["title"] == "Canção Teste" and m["artist"] == "Artista X"
    assert m["codec"] == "mp3" and 4800 <= m["duration_ms"] <= 5300 and m["sample_rate"] == 44100
    assert m["analysis_status"] == "pending"
    run_jobs()
    m = c.get(f"/api/stations/{st['id']}/media/{m['id']}").json()
    assert m["analysis_status"] == "done"
    assert m["loudness_lufs"] is not None and m["true_peak_db"] is not None
    assert 800 <= m["lead_silence_ms"] <= 1200 and m["cue_in_ms"] == m["lead_silence_ms"]  # cue sugerido
    assert m["has_cover"]
    assert c.get(f"/api/stations/{st['id']}/media/{m['id']}/cover").headers["content-type"] == "image/jpeg"
    # pré-escuta por URL assinada, com suporte a Range
    url = c.get(f"/api/stations/{st['id']}/media/{m['id']}/preview-url").json()["url"]
    r = requests.get(base_url + url, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def test_upload_validation(base_url, admin, tmpdir_path):
    c, st = _owner(base_url, admin)
    fake = tmpdir_path / "falso.mp3"
    fake.write_text("isto não é áudio")
    r = upload(c, st["id"], fake)
    assert r.status_code == 422 and r.json()["code"] == "invalid_media"
    exe = tmpdir_path / "malware.exe"
    exe.write_bytes(b"MZ\x90\x00")
    assert upload(c, st["id"], exe).status_code == 415
    good = make_audio(tmpdir_path / "ok.wav", 1.5, 300, codec="wav")
    assert upload(c, st["id"], good).status_code == 200
    r = upload(c, st["id"], good)
    assert r.status_code == 409 and r.json()["code"] == "duplicate"
    # nada do upload inválido ficou no armazenamento/banco
    assert get_db().scalar("SELECT COUNT(*) AS c FROM media WHERE station_id = ?", (st["id"],)) == 1
    assert upload(c, st["id"], make_audio(tmpdir_path / "x.flac", 1, 800, codec="flac")).status_code == 200
    assert upload(c, st["id"], make_audio(tmpdir_path / "x.ogg", 1, 900, codec="ogg")).status_code == 200


def test_edit_cues_and_search(base_url, admin, tmpdir_path):
    c, st = _owner(base_url, admin)
    m = upload(c, st["id"], make_audio(tmpdir_path / "e.mp3", 5, 600, title="Busca Única")).json()
    base = f"/api/stations/{st['id']}/media/{m['id']}"
    assert c.patch(base, {"cue_in_ms": 4800, "cue_out_ms": 4900}).status_code == 422
    assert c.patch(base, {"cue_in_ms": 99999}).status_code == 422
    r = c.patch(base, {"cue_in_ms": 500, "cue_out_ms": 4500, "fade_in_ms": 300, "fade_out_ms": 600,
                       "tags": ["Rock", "anos 80", "rock"], "bpm": 120, "rating": 4, "genre": "Rock"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["effective_ms"] == 4000 and d["tags"] == ["anos 80", "rock"]
    found = c.get(f"/api/stations/{st['id']}/media", params={"q": "busca ún"}).json()
    assert found["total"] == 1
    assert c.get(f"/api/stations/{st['id']}/media", params={"q": "anos 80"}).json()["total"] == 1


def test_license_block_and_queue(base_url, admin, tmpdir_path):
    c, st = _owner(base_url, admin)
    sid = st["id"]
    m1 = upload(c, sid, make_audio(tmpdir_path / "q1.mp3", 2, 400)).json()
    m2 = upload(c, sid, make_audio(tmpdir_path / "q2.mp3", 2, 450)).json()
    c.patch(f"/api/stations/{sid}/media/{m2['id']}", {"license_status": "blocked"})
    r = c.post(f"/api/stations/{sid}/queue", {"media_id": m2["id"]})
    assert r.status_code == 409 and r.json()["code"] == "not_playable"
    c.patch(f"/api/stations/{sid}/media/{m2['id']}", {"license_status": "licensed", "license_expires_at": "2020-01-01"})
    assert c.post(f"/api/stations/{sid}/queue", {"media_id": m2["id"]}).status_code == 409
    a = c.post(f"/api/stations/{sid}/queue", {"media_id": m1["id"]}).json()["added"][0]
    b = c.post(f"/api/stations/{sid}/queue", {"media_id": m1["id"]}).json()["added"][0]
    n = c.post(f"/api/stations/{sid}/queue", {"media_id": m1["id"], "where": "next"}).json()["added"][0]
    order = [i["id"] for i in c.get(f"/api/stations/{sid}/queue").json()["items"]]
    assert order == [n, a, b]
    c.post(f"/api/stations/{sid}/queue/{b}/move", {"index": 0})
    order = [i["id"] for i in c.get(f"/api/stations/{sid}/queue").json()["items"]]
    assert order == [b, n, a]
    assert c.delete(f"/api/stations/{sid}/queue/{n}").status_code == 200
    # mídia com histórico de fila é arquivada (preserva comprovação), não apagada
    assert c.delete(f"/api/stations/{sid}/media/{m1['id']}").json() == {"archived": True}
    assert c.get(f"/api/stations/{sid}/queue").json()["items"] == []


def test_stream_config_never_leaks_password(base_url, admin):
    c, st = _owner(base_url, admin)
    sid = st["id"]
    assert c.post(f"/api/stations/{sid}/stream/start").status_code == 409  # sem senha
    r = c.put(f"/api/stations/{sid}/stream", {"host": "icecast.local", "port": 8000, "mount": "/minha",
                                              "source_password": "SuperSegredo#2026", "format": "opus",
                                              "bitrate_kbps": 96, "public_listen_url": "https://radio.exemplo/minha"})
    assert r.status_code == 200
    body = r.text
    assert "SuperSegredo" not in body and r.json()["has_source_password"]
    assert r.json()["sample_rate"] == 48000  # Opus força 48 kHz
    assert "SuperSegredo" not in c.get(f"/api/stations/{sid}/stream").text
    row = get_db().fetchone("SELECT source_password_enc FROM stream_configs WHERE station_id = ?", (sid,))
    assert "SuperSegredo" not in row["source_password_enc"]
    assert c.put(f"/api/stations/{sid}/stream", {"mount": "sem-barra"}).status_code == 422
    assert c.put(f"/api/stations/{sid}/stream", {"bitrate_kbps": 129}).status_code == 422
    assert c.put(f"/api/stations/{sid}/stream", {"public_listen_url": "javascript:alert(1)"}).status_code == 422
    assert c.post(f"/api/stations/{sid}/stream/start").status_code == 200
    pub = requests.get(f"{base_url}/api/public/stations/{st['slug']}")
    assert pub.status_code == 200
    text = pub.text
    for secret in ("SuperSegredo", "icecast.local", "source_password", "8000"):
        assert secret not in text
    assert pub.json()["on_air"] is False  # sem playout rodando não declara "no ar"
    audit = c.get(f"/api/stations/{sid}/audit").json()["items"]
    assert any(a["action"] == "stream.updated" for a in audit)
    assert all("SuperSegredo" not in (a["details"] or "") for a in audit)


def test_status_and_reports_are_real(base_url, admin):
    c, st = _owner(base_url, admin)
    s = c.get(f"/api/stations/{st['id']}/status").json()
    assert s["status"] == "offline" and s["now_playing"] is None
    assert s["listeners"]["current"] is None  # sem dado real, não inventa número
    assert s["revenue"]["available"] is False
    r = c.get(f"/api/stations/{st['id']}/plays.csv")
    assert r.status_code == 200 and r.text.lstrip("﻿").startswith("inicio_utc;")
    meta = requests.get(base_url + "/api/meta").json()
    assert {m["status"] for m in meta["modules"]} <= {"implemented", "partial", "credentials", "planned"}
