"""Object storage: key convention and the S3 ArtifactStore (ADR 0001, section 2).

Every object of a tenant lives under tenants/<tenant_id>/; code that builds or accepts a key goes through
the helpers so a key can never leave its tenant's prefix. `S3ArtifactStore` is bound to one tenant and keeps
content-addressed bytes at tenants/<tenant_id>/artifacts/<sha256>.

boto3 (synchronous) because the ArtifactStore protocol is synchronous (the engine is); activities that call
it run in threads. aioboto3 would only add an adapter layer.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from hwflow.artifacts import DEFAULT_MEDIA_TYPE, ArtifactNotFound, ArtifactRef, StoreUnavailable

if TYPE_CHECKING:
    from .settings import Settings

CHUNK = 8 * 1024 * 1024  # read size and multipart part size (ADR 2.1)
MULTIPART_THRESHOLD = 64 * 1024 * 1024

_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    return f"tenants/{uuid.UUID(str(tenant_id))}/"


def tenant_key(tenant_id: uuid.UUID, *parts: str) -> str:
    """tenants/<tenant_id>/<part>/<part>...; each part is one safe path segment (no '/', no '..')."""
    if not parts:
        raise ValueError("at least one key segment is required")
    for p in parts:
        if not _SEGMENT.match(p) or p in (".", ".."):
            raise ValueError(f"unsafe key segment: {p!r}")
    return tenant_prefix(tenant_id) + "/".join(parts)


def key_in_tenant(tenant_id: uuid.UUID, key: str) -> bool:
    """True if the key is a normalized key under the tenant's prefix."""
    prefix = tenant_prefix(tenant_id)
    if not key.startswith(prefix):
        return False
    rest = key[len(prefix) :].split("/")
    return all(_SEGMENT.match(p) and p not in (".", "..") for p in rest)


# --- S3 ArtifactStore -------------------------------------------------------------------------------

_SHA = re.compile(r"^[0-9a-f]{64}$")


class ArtifactHashMismatch(ValueError):
    """The bytes do not hash to the expected sha256; nothing was stored (or the object was removed)."""


def make_s3_client(settings: Settings, endpoint_url: str | None = None):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
        config=Config(
            s3={"addressing_style": "path"},
            signature_version="s3v4",
            connect_timeout=5,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class _HashingReader:
    """Non-seekable wrapper that hashes what the S3 transfer reads (the upload is verified in one pass).

    Not seekable on purpose: s3transfer then reads it sequentially, part by part, so the hash is valid.
    """

    def __init__(self, f: BinaryIO):
        self._f = f
        self.sha = hashlib.sha256()
        self.n = 0

    def read(self, size: int = -1) -> bytes:
        b = self._f.read(size)
        self.sha.update(b)
        self.n += len(b)
        return b

    def close(self) -> None:
        self._f.close()


class S3ArtifactStore:
    """ArtifactStore on S3 for ONE tenant: no call can name another tenant's prefix.

    put = hash, HeadObject, upload if absent; the upload is hashed again on the wire and the object is
    removed if the bytes changed meanwhile. Files above `multipart_threshold` use multipart upload with
    `chunk_size` parts; nothing loads a large file into memory.
    """

    def __init__(
        self,
        client,
        bucket: str,
        tenant_id: uuid.UUID | str,
        *,
        multipart_threshold: int = MULTIPART_THRESHOLD,
        chunk_size: int = CHUNK,
    ):
        self.client = client
        self.bucket = bucket
        self.tenant_id = uuid.UUID(str(tenant_id))
        self.multipart_threshold = multipart_threshold
        self.chunk_size = chunk_size

    # -- keys / errors --------------------------------------------------------------------------
    def key(self, sha256: str) -> str:
        if not _SHA.match(sha256):
            raise ValueError(f"not a sha256 (64 lowercase hex): {sha256!r}")
        return tenant_key(self.tenant_id, "artifacts", sha256)

    def _call(self, fn, *a, **kw):
        """Run an S3 call; 404 stays a ClientError, network errors and 5xx become StoreUnavailable."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return fn(*a, **kw)
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            status = int(e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            if code in ("404", "NoSuchKey", "NotFound") or status == 404:
                raise
            raise StoreUnavailable(f"object store error: {code} {status}") from e
        except BotoCoreError as e:  # connection refused, timeout, DNS
            raise StoreUnavailable(f"object store unavailable: {type(e).__name__}: {e}") from e

    def _head(self, sha256: str) -> dict | None:
        from botocore.exceptions import ClientError

        try:
            return self._call(self.client.head_object, Bucket=self.bucket, Key=self.key(sha256))
        except ClientError:  # only 404 gets here (see _call)
            return None

    # -- puts -----------------------------------------------------------------------------------
    def put_bytes(
        self,
        data: bytes,
        *,
        media_type: str = DEFAULT_MEDIA_TYPE,
        name: str = "",
        expected_sha256: str | None = None,
    ) -> ArtifactRef:
        sha = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and expected_sha256 != sha:
            raise ArtifactHashMismatch(f"expected {expected_sha256}, bytes hash to {sha}")
        ref = ArtifactRef(sha, len(data), media_type, name)
        if self._head(sha) is None:
            self._call(
                self.client.put_object,
                Bucket=self.bucket,
                Key=self.key(sha),
                Body=data,
                ContentType=media_type,
            )
        return ref

    def put_file(
        self,
        path: Path,
        *,
        media_type: str = DEFAULT_MEDIA_TYPE,
        name: str = "",
        expected_sha256: str | None = None,
    ) -> ArtifactRef:
        from boto3.s3.transfer import TransferConfig

        path = Path(path)
        h = hashlib.sha256()
        size = 0
        with path.open("rb") as f:
            while chunk := f.read(CHUNK):
                h.update(chunk)
                size += len(chunk)
        sha = h.hexdigest()
        if expected_sha256 is not None and expected_sha256 != sha:
            raise ArtifactHashMismatch(f"expected {expected_sha256}, {path} hashes to {sha}")
        ref = ArtifactRef(sha, size, media_type, name)
        if self._head(sha) is not None:
            return ref
        cfg = TransferConfig(
            multipart_threshold=self.multipart_threshold,
            multipart_chunksize=self.chunk_size,
            max_concurrency=4,
            use_threads=True,
        )
        with path.open("rb") as f:
            reader = _HashingReader(f)
            self._call(
                self.client.upload_fileobj,
                reader,
                self.bucket,
                self.key(sha),
                ExtraArgs={"ContentType": media_type},
                Config=cfg,
            )
        if reader.sha.hexdigest() != sha or reader.n != size:  # the file changed while it was uploaded
            self._call(self.client.delete_object, Bucket=self.bucket, Key=self.key(sha))
            raise ArtifactHashMismatch(f"{path} changed during upload; nothing stored")
        return ref

    # -- reads ----------------------------------------------------------------------------------
    @contextlib.contextmanager
    def open(self, sha256: str) -> Iterator[BinaryIO]:
        """Streaming read of the object body (not verified; get_file verifies)."""
        from botocore.exceptions import ClientError

        try:
            resp = self._call(self.client.get_object, Bucket=self.bucket, Key=self.key(sha256))
        except ClientError as e:
            raise ArtifactNotFound(sha256) from e
        body = resp["Body"]
        try:
            yield body
        finally:
            body.close()

    def get_file(self, sha256: str, dest: Path) -> Path:
        """Download to dest atomically (tmp + rename in the same directory); verifies the hash."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.")
        try:
            h = hashlib.sha256()
            with os.fdopen(fd, "wb") as out, self.open(sha256) as body:
                while chunk := body.read(CHUNK):
                    h.update(chunk)
                    out.write(chunk)
            if h.hexdigest() != sha256:
                raise ArtifactHashMismatch(f"object {sha256} is corrupt: bytes hash to {h.hexdigest()}")
            os.replace(tmp, dest)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        return dest

    def exists(self, sha256: str) -> bool:
        return self._head(sha256) is not None

    def stat(self, sha256: str) -> ArtifactRef | None:
        """Size and media type as first stored (the name is not stored: the ref carries it)."""
        h = self._head(sha256)
        if h is None:
            return None
        return ArtifactRef(sha256, int(h["ContentLength"]), h.get("ContentType") or DEFAULT_MEDIA_TYPE)

    def presigned_get_url(self, sha256: str, *, expires_s: int = 900, filename: str | None = None) -> str:
        """Time-limited GET URL for a download (the API hands this out; the bytes bypass the API)."""
        params = {"Bucket": self.bucket, "Key": self.key(sha256)}
        if filename:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires_s)
