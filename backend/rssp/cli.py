"""Linha de comando administrativa.

  python -m rssp.cli migrate
  python -m rssp.cli create-superadmin --email admin@exemplo.com --name "Admin"
  python -m rssp.cli seed-demo --station demo-thz --owner admin@exemplo.com
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import os
import subprocess
import sys
import tempfile

from .config import get_settings
from .db import get_db, new_id, now_iso
from .jobs import enqueue
from .media import probe
from .security import hash_password, password_problems
from .storage import get_storage


def cmd_migrate(_a) -> None:
    done = get_db().migrate()
    print("migrações aplicadas:", ", ".join(done) if done else "nenhuma (banco atualizado)")


def cmd_create_superadmin(a) -> None:
    db = get_db()
    db.migrate()
    pw = a.password or os.environ.get("RSSP_ADMIN_PASSWORD") or getpass.getpass("Senha: ")
    probs = password_problems(pw)
    if probs:
        sys.exit("senha fraca: " + "; ".join(probs))
    email = a.email.lower()
    if db.fetchone("SELECT id FROM users WHERE email = ?", (email,)):
        sys.exit("já existe usuário com este e-mail")
    db.insert("users", {"id": new_id(), "email": email, "name": a.name, "password_hash": hash_password(pw),
                        "is_superadmin": 1, "is_active": 1, "mfa_secret_enc": None, "mfa_enabled": 0,
                        "failed_logins": 0, "locked_until": None, "created_at": now_iso(), "updated_at": now_iso()})
    print("superadministrador criado:", email)


DEMO_CLIPS = [
    ("music", "DEMO — Tom de teste A4 (440 Hz)", "EmpreenderTHZ (sinal de teste)", "sine=frequency=440:duration=40", "own"),
    ("music", "DEMO — Acorde sintético C-E-G", "EmpreenderTHZ (sinal de teste)",
     "sine=frequency=261.6:duration=35[a];sine=frequency=329.6:duration=35[b];sine=frequency=392:duration=35[c];"
     "[a][b][c]amix=inputs=3", "own"),
    ("music", "DEMO — Varredura 200→2000 Hz", "EmpreenderTHZ (sinal de teste)",
     "aevalsrc='0.4*sin(2*PI*(200+900*t/30)*t)':s=44100:d=30", "own"),
    ("jingle", "DEMO — Vinheta bip duplo", "EmpreenderTHZ", "sine=frequency=880:duration=0.4[a];anullsrc=d=0.2[s];"
     "sine=frequency=1320:duration=0.6[b];[a][s][b]concat=n=3:v=0:a=1", "own"),
    ("music", "DEMO — Ruído rosa suave", "EmpreenderTHZ (sinal de teste)",
     "anoisesrc=color=pink:amplitude=0.15:duration=30", "own"),
]


def cmd_seed_demo(a) -> None:
    """Cria emissora de demonstração com áudios SINTÉTICOS identificados como DEMO (sem música de terceiros)."""
    db = get_db()
    db.migrate()
    owner = db.fetchone("SELECT id FROM users WHERE email = ?", (a.owner.lower(),))
    if not owner:
        sys.exit("proprietário não encontrado; crie-o antes (create-superadmin)")
    st = db.fetchone("SELECT * FROM stations WHERE slug = ?", (a.station,))
    now = now_iso()
    if not st:
        sid = new_id()
        db.insert("stations", {"id": sid, "slug": a.station, "name": "Rádio Demo EmpreenderTHZ",
                               "slogan": "Sua rádio. Sua programação. Seu universo sonoro.",
                               "description": "Emissora de demonstração — conteúdo sintético identificado como DEMO.",
                               "timezone": "America/Sao_Paulo", "primary_color": "#1f8bff", "show_history": 1,
                               "plan": "demo", "is_active": 1, "created_at": now, "updated_at": now})
        db.insert("stream_configs", {"station_id": sid, "mount": f"/{a.station}", "stream_name": "Rádio Demo EmpreenderTHZ",
                                     "updated_at": now})
        db.insert("playout_state", {"station_id": sid, "status": "offline", "updated_at": now})
        db.insert("station_members", {"station_id": sid, "user_id": owner["id"], "role": "owner", "created_at": now})
        st = db.fetchone("SELECT * FROM stations WHERE id = ?", (sid,))
        print("emissora criada:", a.station)
    s = get_settings()
    for kind, title, artist, lavfi, lic in DEMO_CLIPS:
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "clip.mp3")
            subprocess.run([s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", lavfi,
                            "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k",
                            "-metadata", f"title={title}", "-metadata", f"artist={artist}", "-metadata", "comment=DEMO",
                            out], check=True)
            sha = hashlib.sha256(open(out, "rb").read()).hexdigest()
            if db.fetchone("SELECT id FROM media WHERE station_id = ? AND sha256 = ?", (st["id"], sha)):
                continue
            p = probe(out)
            mid = new_id()
            key = f"stations/{st['id']}/media/{mid}.mp3"
            get_storage().put_file(key, out)
            db.insert("media", {"id": mid, "station_id": st["id"], "kind": kind, "title": title, "artist": artist,
                                "tags": '["demo"]', "storage_key": key, "original_filename": "demo.mp3",
                                "container": p.container, "size_bytes": os.path.getsize(out), "sha256": sha,
                                "duration_ms": p.duration_ms, "codec": p.codec, "bitrate_kbps": p.bitrate_kbps,
                                "sample_rate": p.sample_rate, "channels": p.channels, "license_status": lic,
                                "rights_holder": "EmpreenderTHZ", "is_demo": 1, "created_by": owner["id"],
                                "created_at": now_iso(), "updated_at": now_iso()})
            enqueue(db, "analyze_media", {"media_id": mid, "station_id": st["id"]}, st["id"])
            print("mídia demo:", title)
    print("ok — rode o worker (python -m rssp.jobs) para analisar as mídias.")


def main() -> None:
    ap = argparse.ArgumentParser(prog="rssp")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    p = sub.add_parser("create-superadmin")
    p.add_argument("--email", required=True)
    p.add_argument("--name", default="Administrador")
    p.add_argument("--password")
    p.set_defaults(fn=cmd_create_superadmin)
    p = sub.add_parser("seed-demo")
    p.add_argument("--station", default="demo-thz")
    p.add_argument("--owner", required=True)
    p.set_defaults(fn=cmd_seed_demo)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
