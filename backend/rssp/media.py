"""Inspeção e análise de áudio com FFprobe/FFmpeg (processamento offline)."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from .config import get_settings

ALLOWED_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".mp4", ".aac", ".aif", ".aiff"}
ALLOWED_FORMATS = {"mp3", "wav", "flac", "ogg", "mov", "mp4", "m4a", "3gp", "3g2", "mj2", "aac", "aiff"}
ALLOWED_CODECS = {"mp3", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_s16be", "pcm_s24be",
                  "flac", "vorbis", "opus", "aac", "alac"}
MIN_DURATION_MS = 500
MAX_DURATION_MS = 6 * 3600 * 1000


class MediaError(Exception):
    pass


@dataclass
class ProbeResult:
    container: str
    codec: str
    duration_ms: int
    bitrate_kbps: int | None
    sample_rate: int | None
    channels: int | None
    tags: dict
    has_cover: bool


def probe(path: str) -> ProbeResult:
    s = get_settings()
    try:
        out = subprocess.run(
            [s.ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired as e:
        raise MediaError("tempo esgotado ao inspecionar o arquivo") from e
    if out.returncode != 0:
        raise MediaError("arquivo de áudio inválido ou corrompido")
    try:
        info = json.loads(out.stdout or b"{}")
    except json.JSONDecodeError as e:
        raise MediaError("resposta inválida do ffprobe") from e
    fmt = info.get("format", {})
    formats = set((fmt.get("format_name") or "").split(","))
    if not formats & ALLOWED_FORMATS:
        raise MediaError(f"formato de contêiner não suportado: {fmt.get('format_name')}")
    audio = [st for st in info.get("streams", []) if st.get("codec_type") == "audio"]
    if not audio:
        raise MediaError("o arquivo não contém trilha de áudio")
    a = audio[0]
    codec = a.get("codec_name") or ""
    if codec not in ALLOWED_CODECS:
        raise MediaError(f"codec de áudio não suportado: {codec}")
    try:
        duration = float(fmt.get("duration") or a.get("duration") or 0)
    except ValueError:
        duration = 0
    duration_ms = int(duration * 1000)
    if duration_ms < MIN_DURATION_MS:
        raise MediaError("áudio muito curto ou duração indeterminada")
    if duration_ms > MAX_DURATION_MS:
        raise MediaError("áudio excede a duração máxima de 6 horas")
    br = a.get("bit_rate") or fmt.get("bit_rate")
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    tags.update({k.lower(): v for k, v in (a.get("tags") or {}).items() if k.lower() not in tags})
    has_cover = any(st.get("codec_type") == "video" and (st.get("disposition") or {}).get("attached_pic")
                    for st in info.get("streams", []))
    return ProbeResult(
        container=sorted(formats & ALLOWED_FORMATS)[0], codec=codec, duration_ms=duration_ms,
        bitrate_kbps=int(int(br) / 1000) if br and str(br).isdigit() else None,
        sample_rate=int(a["sample_rate"]) if a.get("sample_rate") else None,
        channels=a.get("channels"), tags=tags, has_cover=has_cover)


@dataclass
class Analysis:
    loudness_lufs: float | None
    true_peak_db: float | None
    lead_silence_ms: int
    trail_silence_ms: int


_I_RE = re.compile(r"I:\s+(-?[\d.]+|-inf)\s+LUFS")
_PEAK_RE = re.compile(r"Peak:\s+(-?[\d.]+|-inf)\s+dBFS")
_SS_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SE_RE = re.compile(r"silence_end:\s*(-?[\d.]+)")


def analyze(path: str, duration_ms: int) -> Analysis:
    """Loudness integrado (EBU R128), true peak e silêncio no início/fim."""
    s = get_settings()
    cmd = [s.ffmpeg_bin, "-hide_banner", "-nostats", "-i", path, "-vn",
           "-af", "silencedetect=noise=-50dB:d=0.3,ebur128=peak=true", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=max(120, duration_ms // 1000), check=False)
    err = out.stderr.decode(errors="replace")
    if out.returncode != 0:
        raise MediaError("falha na análise: " + err.strip().splitlines()[-1][:200] if err.strip() else "falha na análise")
    summary = err[err.rfind("Summary:"):] if "Summary:" in err else err
    m_i = _I_RE.search(summary)
    m_p = _PEAK_RE.search(summary)
    lufs = float(m_i.group(1)) if m_i and m_i.group(1) != "-inf" else None
    peak = float(m_p.group(1)) if m_p and m_p.group(1) != "-inf" else None
    starts = [float(x) for x in _SS_RE.findall(err)]
    ends = [float(x) for x in _SE_RE.findall(err)]
    lead = 0
    if starts and starts[0] <= 0.05 and ends:
        lead = int(ends[0] * 1000)
    trail = 0
    if starts:
        last_start = starts[-1]
        # silêncio final: último início sem fim correspondente, ou fim ≈ duração
        if len(ends) < len(starts) or (ends and abs(ends[-1] * 1000 - duration_ms) < 100):
            trail = max(0, duration_ms - int(last_start * 1000))
    if lead >= duration_ms:
        lead, trail = 0, 0
    return Analysis(loudness_lufs=lufs, true_peak_db=peak, lead_silence_ms=lead, trail_silence_ms=trail)


def extract_cover(path: str) -> bytes | None:
    s = get_settings()
    out = subprocess.run([s.ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-i", path, "-an",
                          "-frames:v", "1", "-vf", "scale='min(600,iw)':-2", "-f", "image2", "-c:v", "mjpeg", "pipe:1"],
                         capture_output=True, timeout=60, check=False)
    if out.returncode == 0 and out.stdout[:2] == b"\xff\xd8":
        return out.stdout
    return None


def gain_for(loudness_lufs: float | None, target_lufs: float, true_peak_db: float | None) -> float:
    """Ganho em dB para normalização no playout, limitado para não estourar o true peak (−1 dBTP)."""
    if loudness_lufs is None:
        return 0.0
    gain = target_lufs - loudness_lufs
    if true_peak_db is not None:
        gain = min(gain, -1.0 - true_peak_db)
    return max(-20.0, min(12.0, gain))
