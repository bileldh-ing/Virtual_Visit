import time

from roomscape_api import signing
from roomscape_api.observability import redact
from roomscape_api.security import hash_password, password_problems, token_hash, verify_password


def test_password_hashing_is_argon2_and_verifies():
    h = hash_password("Correct-horse-9")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "Correct-horse-9") and not verify_password(h, "wrong")
    assert not verify_password(None, "x")


def test_password_policy():
    assert password_problems("short")
    assert password_problems("alllowercase123")
    assert not password_problems("Mixed-Case-123")


def test_token_hash_is_sha256_hex():
    assert len(token_hash("abc")) == 64


def test_signed_urls_expire_and_bind_to_object():
    url = signing.file_url("artifact", "11111111-1111-1111-1111-111111111111", "content")
    q = dict(p.split("=") for p in url.split("?")[1].split("&"))
    assert signing.check("artifact", "11111111-1111-1111-1111-111111111111", "content", int(q["exp"]), q["sig"], "")
    assert not signing.check("artifact", "22222222-2222-2222-2222-222222222222", "content", int(q["exp"]), q["sig"], "")
    assert not signing.check("artifact", "11111111-1111-1111-1111-111111111111", "content", int(time.time()) - 1, q["sig"], "")


def test_log_redaction():
    assert "secret" not in redact("GET /x?sig=secretvalue&token=secret2 Bearer secret3")


def _img(fmt: str, size=(640, 480)) -> bytes:
    import io

    from PIL import Image

    b = io.BytesIO()
    Image.new("RGB", size, (90, 120, 150)).save(b, format=fmt)
    return b.getvalue()


def test_upload_input_checks_format_size_and_dimensions():
    import pytest
    from roomscape_api.errors import APIError
    from roomscape_api.media import validate_image

    assert validate_image(_img("JPEG")) == "image/jpeg"
    assert validate_image(_img("PNG")) == "image/png"
    assert validate_image(_img("WEBP")) == "image/webp"
    for bad, code in [(b"GIF89a" + b"\0" * 64, "UNSUPPORTED_MEDIA_TYPE"), (b"%PDF-1.7" + b"\0" * 64, "UNSUPPORTED_MEDIA_TYPE"),
                      (_img("JPEG")[:40], "IMAGE_DECODE_FAILED"), (_img("JPEG", (200, 120)), "IMAGE_TOO_SMALL")]:
        with pytest.raises(APIError) as e:
            validate_image(bad)
        assert e.value.code == code
    with pytest.raises(APIError) as e:
        validate_image(_img("PNG", (2000, 1000)), max_pixels=1_000_000)
    assert e.value.code == "IMAGE_TOO_LARGE"


def test_upload_limit_defaults_to_20_mb():
    from roomscape_api.config import Settings

    assert Settings(secret_key="x" * 32).max_upload_bytes == 20 * 1024 * 1024
