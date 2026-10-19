import io
from typing import Any, Dict, List

import pytest
import requests

from markitdown import FileConversionException, StreamInfo
from markitdown.converters import InstagramConverter

TOKEN = "SECRET-TOKEN-123"
CREDS = {"instagram_access_token": TOKEN, "instagram_user_id": "1784"}

PAYLOAD = {
    "business_discovery": {
        "username": "natgeo",
        "name": "National Geographic",
        "biography": "Experience the world.",
        "website": "https://natgeo.com",
        "followers_count": 100,
        "follows_count": 5,
        "media_count": 2,
        "media": {
            "data": [
                {
                    "caption": "First line\nSecond line",
                    "media_type": "IMAGE",
                    "permalink": "https://www.instagram.com/p/abc/",
                    "timestamp": "2026-01-01T00:00:00+0000",
                    "like_count": 10,
                    "comments_count": 2,
                },
                {"media_type": "VIDEO"},
            ]
        },
    }
}


class FakeResponse:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status_code = status
        self._payload = payload

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, response: Any = None, exc: Any = None) -> None:
        self.response = response or FakeResponse(200, PAYLOAD)
        self.exc = exc
        self.calls: List[Dict[str, Any]] = []

    def get(self, url: str, **kw: Any) -> Any:
        self.calls.append({"url": url, **kw})
        if self.exc:
            raise self.exc
        return self.response


def _info(url: str) -> StreamInfo:
    return StreamInfo(url=url, mimetype="text/html", extension=".html")


def _accepts(url: str, **kw: Any) -> bool:
    return InstagramConverter().accepts(io.BytesIO(b""), _info(url), **kw)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/natgeo/",
        "https://instagram.com/natgeo",
        "http://www.instagram.com/nat.geo_1/?igsh=abc123",
        "https://www.instagram.com/natgeo/#x",
    ],
)
def test_accepts_profile_urls(url: str) -> None:
    assert _accepts(url, **CREDS)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/p/abc123/",
        "https://www.instagram.com/reel/abc123/",
        "https://www.instagram.com/explore/",
        "https://www.instagram.com/accounts/login/",
        "https://www.instagram.com/natgeo/followers/",
        "https://evil.com/www.instagram.com/natgeo/",
        "https://notinstagram.com/natgeo/",
        "https://www.instagram.com/",
        "",
    ],
)
def test_rejects_non_profile_urls(url: str) -> None:
    assert not _accepts(url, **CREDS)


def test_declines_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTAGRAM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("INSTAGRAM_USER_ID", raising=False)
    assert not _accepts("https://www.instagram.com/natgeo/")
    # token without user id is still unusable
    assert not _accepts(
        "https://www.instagram.com/natgeo/", instagram_access_token=TOKEN
    )


def test_credentials_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("INSTAGRAM_USER_ID", "1784")
    assert _accepts("https://www.instagram.com/natgeo/")


def test_convert_renders_profile_and_hides_token() -> None:
    session = FakeSession()
    result = InstagramConverter().convert(
        io.BytesIO(b""),
        _info("https://www.instagram.com/natgeo/?igsh=zzz"),
        requests_session=session,
        **CREDS,
    )
    md = result.markdown
    assert "# National Geographic (@natgeo)" in md
    assert "Experience the world." in md
    assert "**Followers:** 100" in md
    assert "**Website:** https://natgeo.com" in md
    assert "[First line](https://www.instagram.com/p/abc/)" in md
    assert "10 likes" in md and "2 comments" in md
    assert "Second line" in md
    assert result.title == "National Geographic"

    call = session.calls[0]
    assert call["url"].endswith("/1784")
    assert "business_discovery.username(natgeo)" in call["params"]["fields"]
    assert "media.limit(10)" in call["params"]["fields"]
    # token only in the Authorization header: never in URL, params or output
    assert TOKEN not in call["url"]
    assert TOKEN not in str(call["params"])
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in md


def test_media_limit_is_clamped_and_zero_omits_media() -> None:
    s = FakeSession()
    InstagramConverter().convert(
        io.BytesIO(b""),
        _info("https://www.instagram.com/natgeo/"),
        requests_session=s,
        instagram_media_limit=9999,
        **CREDS,
    )
    assert "media.limit(50)" in s.calls[0]["params"]["fields"]

    s = FakeSession()
    InstagramConverter().convert(
        io.BytesIO(b""),
        _info("https://www.instagram.com/natgeo/"),
        requests_session=s,
        instagram_media_limit=0,
        **CREDS,
    )
    assert "media.limit" not in s.calls[0]["params"]["fields"]


def test_api_error_is_reported_without_token() -> None:
    session = FakeSession(
        FakeResponse(400, {"error": {"code": 110, "message": "Invalid user id"}})
    )
    with pytest.raises(FileConversionException) as exc:
        InstagramConverter().convert(
            io.BytesIO(b""),
            _info("https://www.instagram.com/natgeo/"),
            requests_session=session,
            **CREDS,
        )
    assert "110" in str(exc.value) and "Invalid user id" in str(exc.value)
    assert TOKEN not in str(exc.value)


def test_non_business_target_is_reported() -> None:
    session = FakeSession(FakeResponse(200, {"id": "1"}))
    with pytest.raises(FileConversionException, match="business or creator"):
        InstagramConverter().convert(
            io.BytesIO(b""),
            _info("https://www.instagram.com/natgeo/"),
            requests_session=session,
            **CREDS,
        )


def test_network_failure_does_not_leak_details() -> None:
    session = FakeSession(exc=requests.ConnectionError(f"boom {TOKEN}"))
    with pytest.raises(FileConversionException) as exc:
        InstagramConverter().convert(
            io.BytesIO(b""),
            _info("https://www.instagram.com/natgeo/"),
            requests_session=session,
            **CREDS,
        )
    assert "ConnectionError" in str(exc.value)
    assert TOKEN not in str(exc.value)
    assert TOKEN not in repr(exc.value.__cause__)


def test_non_json_response_is_handled() -> None:
    session = FakeSession(FakeResponse(502, None))
    with pytest.raises(FileConversionException, match="HTTP 502"):
        InstagramConverter().convert(
            io.BytesIO(b""),
            _info("https://www.instagram.com/natgeo/"),
            requests_session=session,
            **CREDS,
        )
