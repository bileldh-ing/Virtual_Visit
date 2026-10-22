"""Private object storage: S3-compatible (MinIO/AWS) or local filesystem (dev/test only).

Keys are generated server side from UUIDs only; user-supplied names never
become keys. Clients never receive raw storage URLs: files are served through
the API's signed, revocation-aware ``/files`` endpoint.
"""

from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Protocol

from .config import get_settings


class Storage(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str) -> None: ...
    def put_file(self, key: str, path: Path, content_type: str) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def download(self, key: str, dest: Path) -> None: ...
    def iter_chunks(self, key: str, chunk: int = 1 << 16) -> Iterator[bytes]: ...
    def size(self, key: str) -> int | None: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str) -> list[str]: ...
    def health(self) -> bool: ...


def _check_key(key: str) -> str:
    if key.startswith("/") or ".." in key.split("/") or "\\" in key:
        raise ValueError("invalid storage key")
    return key


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if self.root not in p.parents:
            raise ValueError("invalid storage key")
        return p

    def put_bytes(self, key, data, content_type):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def put_file(self, key, path, content_type):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, p)

    def get_bytes(self, key):
        return self._p(key).read_bytes()

    def download(self, key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._p(key), dest)

    def iter_chunks(self, key, chunk=1 << 16):
        with self._p(key).open("rb") as fh:
            while b := fh.read(chunk):
                yield b

    def size(self, key):
        p = self._p(key)
        return p.stat().st_size if p.exists() else None

    def delete(self, key):
        p = self._p(key)
        if p.exists():
            p.unlink()

    def list(self, prefix):
        base = self._p(prefix.rstrip("/")) if prefix else self.root
        if not base.exists():
            return []
        if base.is_file():
            return [prefix]
        return sorted(str(f.relative_to(self.root)) for f in base.rglob("*") if f.is_file())

    def health(self):
        return self.root.exists()


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3", endpoint_url=s.s3_endpoint_url, region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key, aws_secret_access_key=s.s3_secret_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def ensure_bucket(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put_bytes(self, key, data, content_type):
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=content_type)

    def put_file(self, key, path, content_type):
        self.client.upload_file(str(path), self.bucket, _check_key(key), ExtraArgs={"ContentType": content_type})

    def get_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()

    def download(self, key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, _check_key(key), str(dest))

    def iter_chunks(self, key, chunk=1 << 16):
        body = self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"]
        yield from body.iter_chunks(chunk)

    def size(self, key):
        try:
            return self.client.head_object(Bucket=self.bucket, Key=_check_key(key))["ContentLength"]
        except Exception:
            return None

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def list(self, prefix):
        keys, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kw)
            keys += [o["Key"] for o in resp.get("Contents", [])]
            if not resp.get("IsTruncated"):
                return keys
            token = resp["NextContinuationToken"]

    def health(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return True
        except Exception:
            return False


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        st = S3Storage()
        st.ensure_bucket()
        return st
    if not s.is_dev:
        raise RuntimeError("Local filesystem storage is for development/test only; set ROOMSCAPE_STORAGE_BACKEND=s3")
    return LocalStorage(s.storage_local_root)


def project_prefix(org_id, project_id) -> str:
    return f"orgs/{org_id}/projects/{project_id}/"
