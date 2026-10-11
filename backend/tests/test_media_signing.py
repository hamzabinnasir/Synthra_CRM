"""Media serving: signed public URLs for Meta and safe download headers."""
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from app.api import media

from tests.conftest import make_settings


def test_signed_url_carries_a_valid_short_lived_signature():
    settings, tenant, media_id = make_settings(), uuid.uuid4(), str(uuid.uuid4())
    parts = urlsplit(media.signed_media_url(settings, tenant, media_id))
    query = parse_qs(parts.query)
    expires = int(query["exp"][0])
    assert f"{parts.scheme}://{parts.netloc}" == "https://api.example.com"
    assert parts.path == f"/public/media/{tenant}/{media_id}"
    assert 0 < expires - time.time() <= media.SIGNED_TTL_SECONDS
    assert query["sig"][0] == media._signature(settings, tenant, media_id, expires)


def test_signature_is_bound_to_tenant_media_expiry_and_secret():
    settings, tenant, media_id = make_settings(), uuid.uuid4(), str(uuid.uuid4())
    base = media._signature(settings, tenant, media_id, 1_000)
    assert base != media._signature(settings, uuid.uuid4(), media_id, 1_000)
    assert base != media._signature(settings, tenant, str(uuid.uuid4()), 1_000)
    assert base != media._signature(settings, tenant, media_id, 1_001)
    assert base != media._signature(make_settings(CRM_JWT_SECRET="k" * 40), tenant, media_id, 1_000)


def test_unsafe_types_are_forced_to_download():
    record = {"mime_type": "text/html", "filename": 'evil".html\r\n', "data": b"<script>alert(1)</script>"}
    response = media._file_response(record, inline_types=media.INLINE)
    assert response.headers["content-type"].startswith("application/octet-stream")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert '"' not in response.headers["content-disposition"].split("filename=")[1].strip('"')
    assert response.headers["x-content-type-options"] == "nosniff"


def test_images_render_inline_and_public_route_only_allows_jpeg_and_png():
    record = {"mime_type": "image/png", "filename": "a.png", "data": b"\x89PNG"}
    assert media._file_response(record, inline_types=media.INLINE).headers["content-disposition"].startswith("inline;")
    assert media.PUBLIC_TYPES == {"image/jpeg", "image/png"}
    pdf = {"mime_type": "application/pdf", "filename": "a.pdf", "data": b"%PDF"}
    assert media._file_response(pdf, inline_types=media.PUBLIC_TYPES).headers["content-disposition"].startswith("attachment;")
