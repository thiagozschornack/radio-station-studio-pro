import time

import requests

from conftest import PASSWORD, Client, make_user, new_station
from rssp.db import get_db
from rssp.security import (RateLimiter, decrypt, encrypt, hash_password, sign_media_url, totp_code, verify_password,
                           verify_totp)


def test_password_hash_roundtrip():
    h = hash_password("Abcdefgh123")
    assert h.startswith("scrypt$") and "Abcdefgh123" not in h
    assert verify_password("Abcdefgh123", h)
    assert not verify_password("abcdefgh123", h)


def test_encryption_roundtrip_and_tamper():
    tok = encrypt("segredo-icecast")
    assert "segredo" not in tok
    assert decrypt(tok) == "segredo-icecast"
    assert decrypt(tok[:-4] + "AAAA") is None


def test_totp_rfc6238_vector():
    # Vetor de teste RFC 6238 (SHA1, segredo "12345678901234567890"), t=59 → 94287082 (8 dígitos)
    import base64
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp_code(secret, 59, digits=8) == "94287082"
    assert verify_totp(secret, totp_code(secret))


def test_rate_limiter():
    rl = RateLimiter()
    assert all(rl.allow("k", 3) for _ in range(3))
    assert not rl.allow("k", 3)


def test_login_logout_refresh_rotation(base_url):
    u = make_user()
    s = requests.Session()
    r = s.post(base_url + "/api/auth/login", json={"email": u["email"], "password": PASSWORD})
    assert r.status_code == 200
    assert "rssp_refresh" in s.cookies
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=strict" in set_cookie
    old = s.cookies["rssp_refresh"]
    r2 = s.post(base_url + "/api/auth/refresh")
    assert r2.status_code == 200 and r2.json()["access_token"]
    # reuso do token antigo → revoga todas as sessões
    r3 = requests.post(base_url + "/api/auth/refresh", cookies={"rssp_refresh": old})
    assert r3.status_code == 401
    r4 = s.post(base_url + "/api/auth/refresh")
    assert r4.status_code == 401


def test_bad_password_and_lockout(base_url):
    u = make_user()
    for _ in range(5):
        r = requests.post(base_url + "/api/auth/login", json={"email": u["email"], "password": "errada"})
        assert r.status_code == 401
    r = requests.post(base_url + "/api/auth/login", json={"email": u["email"], "password": PASSWORD})
    assert r.status_code == 423


def test_unknown_email_same_message(base_url):
    r = requests.post(base_url + "/api/auth/login", json={"email": "naoexiste@x.dev", "password": "x"})
    assert r.status_code == 401 and r.json()["error"] == "e-mail ou senha inválidos"


def test_requires_auth(base_url):
    assert requests.get(base_url + "/api/me").status_code == 401
    assert requests.get(base_url + "/api/me", headers={"Authorization": "Bearer lixo"}).status_code == 401


def test_mfa_flow(base_url):
    u = make_user()
    c = Client(base_url, u["email"])
    setup = c.post("/api/me/mfa/setup").json()
    assert c.post("/api/me/mfa/enable", {"code": "000000"}).status_code == 422
    assert c.post("/api/me/mfa/enable", {"code": totp_code(setup["secret"])}).status_code == 200
    r = requests.post(base_url + "/api/auth/login", json={"email": u["email"], "password": PASSWORD})
    body = r.json()
    assert body.get("mfa_required") and "access_token" not in body
    assert requests.post(base_url + "/api/auth/mfa", json={"challenge": body["challenge"], "code": "123456"}).status_code == 401
    r = requests.post(base_url + "/api/auth/mfa", json={"challenge": body["challenge"], "code": totp_code(setup["secret"])})
    assert r.status_code == 200 and r.json()["access_token"]
    # segredo cifrado em repouso
    row = get_db().fetchone("SELECT mfa_secret_enc FROM users WHERE id = ?", (u["id"],))
    assert setup["secret"] not in row["mfa_secret_enc"]


def test_change_password_policy(base_url):
    u = make_user()
    c = Client(base_url, u["email"])
    assert c.post("/api/me/password", {"current_password": PASSWORD, "new_password": "fraca"}).status_code == 422
    assert c.post("/api/me/password", {"current_password": "x", "new_password": "NovaSenha12345"}).status_code == 401
    assert c.post("/api/me/password", {"current_password": PASSWORD, "new_password": "NovaSenha12345"}).status_code == 200
    Client(base_url, u["email"], "NovaSenha12345")


def test_security_headers(base_url):
    r = requests.get(base_url + "/admin/")
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert requests.get(base_url + "/api/health").headers["cache-control"] == "no-store"


def test_signed_media_url_expiry(base_url, admin):
    st = new_station(admin)
    url = sign_media_url(st["id"], "x", ttl=-1)
    assert requests.get(base_url + url).status_code == 403


def test_superadmin_only_routes(base_url):
    u = make_user()
    c = Client(base_url, u["email"])
    assert c.get("/api/admin/users").status_code == 403
    assert c.post("/api/stations", {"slug": "nope-123", "name": "x"}).status_code == 403
    _ = time
