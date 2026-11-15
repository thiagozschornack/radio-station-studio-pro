"""Armazenamento de arquivos: disco local (dev/servidor único) ou S3 compatível.

As chaves são sempre geradas pelo sistema: `stations/<station_id>/media/<uuid>.<ext>`.
Nunca usamos o nome enviado pelo usuário como caminho.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO

from .config import get_settings


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("chave de armazenamento inválida")
        return p

    def put_file(self, key: str, src: str) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    def put_bytes(self, key: str, data: bytes) -> None:
        dst = self._path(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)

    def local_path(self, key: str) -> str:
        """Caminho local legível por FFmpeg. No S3, baixa para cache."""
        return str(self._path(key))

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def size(self, key: str) -> int:
        return self._path(key).stat().st_size

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:  # pragma: no cover - depende de credenciais e do pacote boto3
    """Integração dependente de credenciais (boto3 + bucket S3/MinIO). Não testada neste pacote."""

    def __init__(self, bucket: str, endpoint: str | None, region: str, cache_dir: str):
        import boto3  # type: ignore
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint or None, region_name=region)
        self.cache = LocalStorage(cache_dir)

    def put_file(self, key: str, src: str) -> None:
        self.client.upload_file(src, self.bucket, key)

    def put_bytes(self, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def local_path(self, key: str) -> str:
        if not self.cache.exists(key):
            p = Path(self.cache._path(key))
            p.parent.mkdir(parents=True, exist_ok=True)
            self.client.download_file(self.bucket, key, str(p))
        return self.cache.local_path(key)

    def open(self, key: str) -> BinaryIO:
        return open(self.local_path(key), "rb")

    def size(self, key: str) -> int:
        return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        self.cache.delete(key)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage = None


def get_storage():
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            _storage = S3Storage(s.s3_bucket, s.s3_endpoint, s.s3_region, s.storage_path + "/_s3cache")
        else:
            _storage = LocalStorage(s.storage_path)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
