"""S3ArtifactStore against the compose SeaweedFS (ADR 0001, section 2; issue #6)."""

import hashlib
import os
import urllib.error
import urllib.request
import uuid

import pytest
from hwflow.artifacts import ArtifactNotFound, StoreUnavailable

from hwplatform.storage import ArtifactHashMismatch, S3ArtifactStore, tenant_key, tenant_prefix

from conftest import S3_BUCKET

pytestmark = pytest.mark.integration


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_bytes_roundtrip_stat_exists(s3_store, tmp_path):
    st = s3_store()
    data = b"hello artifact " + os.urandom(16)
    ref = st.put_bytes(data, media_type="text/plain", name="greeting")
    assert (ref.sha256, ref.size, ref.media_type, ref.name) == (
        sha(data),
        len(data),
        "text/plain",
        "greeting",
    )
    assert st.exists(ref.sha256)
    stat = st.stat(ref.sha256)
    assert stat is not None and stat.size == len(data) and stat.media_type == "text/plain"
    with st.open(ref.sha256) as f:
        assert f.read() == data
    dest = st.get_file(ref.sha256, tmp_path / "sub" / "out.bin")
    assert dest.read_bytes() == data
    assert [p.name for p in (tmp_path / "sub").iterdir()] == ["out.bin"]  # no tmp file left behind
    # the object lives at the ADR key
    assert st.key(ref.sha256) == f"tenants/{st.tenant_id}/artifacts/{ref.sha256}"
    assert st.key(ref.sha256) == tenant_key(st.tenant_id, "artifacts", ref.sha256)


def test_put_is_idempotent_and_keeps_first_media_type(s3_store):
    st = s3_store()
    data = os.urandom(64)
    r1 = st.put_bytes(data, media_type="application/x-first")
    r2 = st.put_bytes(data, media_type="application/x-second")
    assert r1.sha256 == r2.sha256
    assert st.stat(r1.sha256).media_type == "application/x-first"  # "as first stored"


def test_missing_object(s3_store, tmp_path):
    st = s3_store()
    ghost = sha(b"never stored " + os.urandom(8))
    assert not st.exists(ghost) and st.stat(ghost) is None
    with pytest.raises(ArtifactNotFound), st.open(ghost):
        pass
    with pytest.raises(ArtifactNotFound):
        st.get_file(ghost, tmp_path / "x")
    assert not (tmp_path / "x").exists() and list(tmp_path.iterdir()) == []


def test_file_roundtrip_and_multipart(s3_store, s3_client, tmp_path):
    # 12 MiB with a 5 MiB threshold and part size forces the multipart path (3 parts).
    st = s3_store(multipart_threshold=5 * 1024 * 1024, chunk_size=5 * 1024 * 1024)
    p = tmp_path / "big.bin"
    p.write_bytes(os.urandom(12 * 1024 * 1024))
    ref = st.put_file(p, media_type="application/x-big", name="big")
    assert ref.sha256 == sha(p.read_bytes()) and ref.size == p.stat().st_size
    head = s3_client.head_object(Bucket=S3_BUCKET, Key=st.key(ref.sha256))
    assert "-" in head["ETag"].strip('"'), "expected a multipart ETag (<md5>-<parts>)"
    out = st.get_file(ref.sha256, tmp_path / "back.bin")
    assert out.read_bytes() == p.read_bytes()
    # a small file takes the single-part path
    small = tmp_path / "small.txt"
    small.write_text("small")
    r2 = st.put_file(small)
    assert st.exists(r2.sha256) and r2.size == 5


def test_sha_mismatch_rejected_on_put(s3_store):
    st = s3_store()
    data = b"payload " + os.urandom(8)
    wrong = sha(b"something else")
    with pytest.raises(ArtifactHashMismatch):
        st.put_bytes(data, expected_sha256=wrong)
    assert not st.exists(wrong) and not st.exists(sha(data))  # nothing stored under either hash
    assert st.put_bytes(data, expected_sha256=sha(data)).sha256 == sha(data)


def test_sha_mismatch_rejected_on_put_file(s3_store, tmp_path):
    st = s3_store()
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc" + os.urandom(8))
    with pytest.raises(ArtifactHashMismatch):
        st.put_file(p, expected_sha256=sha(b"other"))
    assert not st.exists(sha(p.read_bytes()))


def test_corrupt_object_is_detected_on_download(s3_store, s3_client, tmp_path):
    st = s3_store()
    ref = st.put_bytes(b"good bytes " + os.urandom(8))
    s3_client.put_object(Bucket=S3_BUCKET, Key=st.key(ref.sha256), Body=b"tampered")
    dest = tmp_path / "dl.bin"
    with pytest.raises(ArtifactHashMismatch):
        st.get_file(ref.sha256, dest)
    assert not dest.exists() and list(tmp_path.iterdir()) == []


def test_bad_sha_is_not_a_key(s3_store):
    st = s3_store()
    for bad in ("../../etc/passwd", "ABC", "a" * 63, "a/" + "b" * 62, ""):
        with pytest.raises(ValueError):
            st.key(bad)
        with pytest.raises(ValueError):
            st.exists(bad)


def test_cross_tenant_isolation(s3_store, s3_client):
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    a, b = s3_store(a_id), s3_store(b_id)
    secret = b"tenant A secret " + os.urandom(16)
    ref = a.put_bytes(secret, media_type="text/plain")
    # B's store, same sha: nothing there
    assert a.exists(ref.sha256) and not b.exists(ref.sha256)
    assert b.stat(ref.sha256) is None
    with pytest.raises(ArtifactNotFound), b.open(ref.sha256):
        pass
    # identical bytes uploaded by B are a separate object under B's prefix (no cross-tenant dedup)
    b.put_bytes(secret)
    assert a.key(ref.sha256) != b.key(ref.sha256)
    for t, store in ((a_id, a), (b_id, b)):
        keys = [
            o["Key"]
            for o in s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=tenant_prefix(t)).get("Contents", [])
        ]
        assert keys == [store.key(ref.sha256)]
    # deleting through B never touches A
    s3_client.delete_object(Bucket=S3_BUCKET, Key=b.key(ref.sha256))
    assert a.exists(ref.sha256) and not b.exists(ref.sha256)


def test_presigned_get_url(s3_store):
    st = s3_store()
    data = b"download me " + os.urandom(8)
    ref = st.put_bytes(data, media_type="text/plain")
    url = st.presigned_get_url(ref.sha256, expires_s=60, filename="report v1.txt")
    with urllib.request.urlopen(url, timeout=10) as r:  # noqa: S310
        assert r.read() == data
        assert "attachment" in r.headers.get("Content-Disposition", "")
    # a URL for another tenant's store does not reach this object, and a URL for a missing one is 404
    other = s3_store()
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(other.presigned_get_url(ref.sha256, expires_s=60), timeout=10)  # noqa: S310
    assert e.value.code == 404


def test_store_unavailable_is_retryable_error(tmp_path):
    import boto3
    from botocore.config import Config

    dead = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:1",
        aws_access_key_id="x",
        aws_secret_access_key="y",
        region_name="us-east-1",
        config=Config(connect_timeout=1, retries={"max_attempts": 1}),
    )
    st = S3ArtifactStore(dead, "b", uuid.uuid4())
    with pytest.raises(StoreUnavailable):
        st.put_bytes(b"x")
    with pytest.raises(StoreUnavailable):
        st.exists(sha(b"x"))
