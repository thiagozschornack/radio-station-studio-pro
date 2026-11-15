"""Fila de trabalho persistente no banco (jobs) e o worker que a consome.

Na Fase 1 a fila mora no PostgreSQL (SELECT ... FOR UPDATE SKIP LOCKED), o que dispensa Redis
para garantir durabilidade. Redis entra como cache/pub-sub em fases seguintes.

Execução:  python -m rssp.jobs
"""
from __future__ import annotations

import json
import logging
import signal
import time

from .db import Database, dumps, get_db, new_id, now_iso
from .media import MediaError, analyze, extract_cover
from .storage import get_storage

log = logging.getLogger("rssp.worker")


def enqueue(db: Database, kind: str, payload: dict, station_id: str | None = None, delay: float = 0) -> str:
    jid = new_id()
    db.insert("jobs", {"id": jid, "kind": kind, "station_id": station_id, "payload": dumps(payload),
                       "status": "queued", "attempts": 0, "max_attempts": 3, "run_after": now_iso(delay),
                       "created_at": now_iso()})
    return jid


def claim(db: Database) -> dict | None:
    now = now_iso()
    stale = now_iso(-600)  # jobs presos há 10 min (worker morto) voltam a ser elegíveis
    with db.transaction(immediate=True):
        if db.dialect == "postgres":
            row = db.fetchone(
                "SELECT * FROM jobs WHERE (status = 'queued' AND run_after <= ?) OR (status = 'running' AND locked_at < ?) "
                "ORDER BY run_after LIMIT 1 FOR UPDATE SKIP LOCKED", (now, stale))
        else:
            row = db.fetchone(
                "SELECT * FROM jobs WHERE (status = 'queued' AND run_after <= ?) OR (status = 'running' AND locked_at < ?) "
                "ORDER BY run_after LIMIT 1", (now, stale))
        if not row:
            return None
        db.update("jobs", {"status": "running", "locked_at": now, "attempts": row["attempts"] + 1}, "id = ?", (row["id"],))
        row["attempts"] += 1
        return row


def finish(db: Database, job: dict, error: str | None = None) -> None:
    if error is None:
        db.update("jobs", {"status": "done", "finished_at": now_iso(), "last_error": None}, "id = ?", (job["id"],))
    elif job["attempts"] >= job["max_attempts"]:
        db.update("jobs", {"status": "failed", "finished_at": now_iso(), "last_error": error[:1000]}, "id = ?", (job["id"],))
    else:
        db.update("jobs", {"status": "queued", "run_after": now_iso(30 * job["attempts"]), "last_error": error[:1000]},
                  "id = ?", (job["id"],))


# ------------------------------------------------------------------ handlers
def handle_analyze_media(db: Database, payload: dict, final_attempt: bool) -> None:
    m = db.fetchone("SELECT * FROM media WHERE id = ? AND station_id = ?", (payload["media_id"], payload["station_id"]))
    if not m:
        return
    st = get_storage()
    path = st.local_path(m["storage_key"])
    try:
        res = analyze(path, m["duration_ms"])
    except MediaError as e:
        if final_attempt:
            db.update("media", {"analysis_status": "failed", "analysis_error": str(e)[:500], "updated_at": now_iso()},
                      "id = ?", (m["id"],))
        raise
    values = {"loudness_lufs": res.loudness_lufs, "true_peak_db": res.true_peak_db,
              "lead_silence_ms": res.lead_silence_ms, "trail_silence_ms": res.trail_silence_ms,
              "analysis_status": "done", "analysis_error": None, "updated_at": now_iso()}
    # Pontos de entrada/saída sugeridos apenas se o usuário não editou (edição não destrutiva).
    if m["cue_in_ms"] == 0 and res.lead_silence_ms:
        values["cue_in_ms"] = res.lead_silence_ms
    if m["cue_out_ms"] is None and res.trail_silence_ms:
        values["cue_out_ms"] = m["duration_ms"] - res.trail_silence_ms
    if not m["cover_key"]:
        cover = extract_cover(path)
        if cover:
            key = f"stations/{m['station_id']}/covers/{m['id']}.jpg"
            st.put_bytes(key, cover)
            values["cover_key"] = key
    db.update("media", values, "id = ?", (m["id"],))


HANDLERS = {"analyze_media": handle_analyze_media}


def run_once(db: Database | None = None) -> bool:
    db = db or get_db()
    job = claim(db)
    if not job:
        return False
    handler = HANDLERS.get(job["kind"])
    try:
        if handler is None:
            raise RuntimeError(f"tipo de job desconhecido: {job['kind']}")
        handler(db, json.loads(job["payload"]), job["attempts"] >= job["max_attempts"])
        finish(db, job)
        log.info("job %s (%s) concluído", job["id"], job["kind"])
    except Exception as e:  # noqa: BLE001
        log.warning("job %s (%s) falhou: %s", job["id"], job["kind"], e)
        finish(db, job, str(e))
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db = get_db()
    db.migrate()
    stop = {"v": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(v=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(v=True))
    log.info("worker iniciado")
    while not stop["v"]:
        if not run_once(db):
            time.sleep(1.0)
    log.info("worker encerrado")


if __name__ == "__main__":
    main()
