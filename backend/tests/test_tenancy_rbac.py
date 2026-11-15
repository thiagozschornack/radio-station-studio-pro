"""Isolamento entre emissoras (multi-tenant) e permissões por papel."""
import requests

from conftest import Client, make_audio, make_user, new_station, upload
from rssp.security import sign_media_url


def _setup(base_url, admin, tmpdir_path):
    a, b = make_user(), make_user()
    sa = new_station(admin, a["email"])
    sb = new_station(admin, b["email"])
    ca, cb = Client(base_url, a["email"]), Client(base_url, b["email"])
    f = make_audio(tmpdir_path / "a.mp3", 2, 500)
    media_a = upload(ca, sa["id"], f).json()
    return ca, cb, sa, sb, media_a


def test_cross_tenant_access_denied(base_url, admin, tmpdir_path):
    ca, cb, sa, sb, ma = _setup(base_url, admin, tmpdir_path)
    for path in ("", "/status", "/media", "/queue", "/stream", "/plays", "/members", "/audit",
                 f"/media/{ma['id']}", f"/media/{ma['id']}/preview-url"):
        r = cb.get(f"/api/stations/{sa['id']}{path}")
        assert r.status_code == 404, (path, r.status_code)
    assert cb.patch(f"/api/stations/{sa['id']}/media/{ma['id']}", {"title": "hack"}).status_code == 404
    assert cb.delete(f"/api/stations/{sa['id']}/media/{ma['id']}").status_code == 404
    assert cb.post(f"/api/stations/{sa['id']}/stream/start").status_code == 404
    # usar mídia de A na fila de B (ID válido de outro tenant)
    r = cb.post(f"/api/stations/{sb['id']}/queue", {"media_id": ma["id"]})
    assert r.status_code == 404
    # configurar mídia de contingência de outro tenant
    assert cb.put(f"/api/stations/{sb['id']}/stream", {"fallback_media_id": ma["id"]}).status_code == 422
    # URL assinada para A não vale trocando a emissora
    url = sign_media_url(sa["id"], ma["id"]).replace(sa["id"], sb["id"])
    assert requests.get(ca.base + url).status_code == 403
    # me lista apenas as próprias emissoras
    ids = [s["id"] for s in cb.get("/api/me").json()["stations"]]
    assert sb["id"] in ids and sa["id"] not in ids


def test_roles(base_url, admin, tmpdir_path):
    owner = make_user()
    st = new_station(admin, owner["email"])
    co = Client(base_url, owner["email"])
    roles = {}
    for role in ("analyst", "operator", "programmer", "announcer", "commercial"):
        u = make_user()
        assert co.post(f"/api/stations/{st['id']}/members", {"email": u["email"], "role": role}).status_code == 200
        roles[role] = Client(base_url, u["email"])
    f = make_audio(tmpdir_path / "r.mp3", 2, 700)
    assert upload(roles["analyst"], st["id"], f).status_code == 403
    assert upload(roles["operator"], st["id"], f).status_code == 403
    r = upload(roles["programmer"], st["id"], f)
    assert r.status_code == 200
    mid = r.json()["id"]
    assert roles["analyst"].get(f"/api/stations/{st['id']}/plays").status_code == 200
    assert roles["analyst"].get(f"/api/stations/{st['id']}/queue").status_code == 403
    assert roles["operator"].put(f"/api/stations/{st['id']}/stream", {"port": 9000}).status_code == 403
    assert roles["operator"].post(f"/api/stations/{st['id']}/stream/stop").status_code == 200
    assert roles["announcer"].post(f"/api/stations/{st['id']}/playout/skip").status_code == 200
    assert roles["announcer"].post(f"/api/stations/{st['id']}/queue", {"media_id": mid}).status_code == 403
    # programador não altera licença
    assert roles["programmer"].patch(f"/api/stations/{st['id']}/media/{mid}", {"license_status": "licensed"}).status_code == 403
    assert co.patch(f"/api/stations/{st['id']}/media/{mid}", {"license_status": "licensed"}).status_code == 200
    # gestão de membros apenas pelo proprietário
    assert roles["programmer"].post(f"/api/stations/{st['id']}/members", {"email": "x@y.dev", "role": "owner"}).status_code == 403
    # último proprietário não pode ser removido
    assert co.delete(f"/api/stations/{st['id']}/members/{owner['id']}").status_code == 409


def test_owner_invites_new_user_with_temp_password(base_url, admin):
    owner = make_user()
    st = new_station(admin, owner["email"])
    co = Client(base_url, owner["email"])
    r = co.post(f"/api/stations/{st['id']}/members", {"email": "novo-locutor@teste.dev", "role": "announcer"})
    tmp = r.json()["temporary_password"]
    assert tmp
    Client(base_url, "novo-locutor@teste.dev", tmp)
