import os
import re
from typing import Any, BinaryIO, Dict, List, Optional
from urllib.parse import unquote, urlparse

import requests

from .._base_converter import DocumentConverter, DocumentConverterResult
from .._exceptions import FileConversionException
from .._stream_info import StreamInfo

GRAPH_API_VERSION = "v21.0"
GRAPH_API_BASE = "https://graph.facebook.com"
DEFAULT_MEDIA_LIMIT = 10
REQUEST_TIMEOUT = 30

# instagram.com paths that are not user profiles
_RESERVED_PATHS = {
    "p",
    "reel",
    "reels",
    "tv",
    "stories",
    "explore",
    "accounts",
    "direct",
    "about",
    "legal",
    "developer",
}

_PROFILE_URL = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?P<username>[A-Za-z0-9._]{1,30})/?$"
)

_PROFILE_FIELDS = (
    "username,name,biography,website,followers_count,follows_count,media_count"
)
_MEDIA_FIELDS = "caption,media_type,permalink,timestamp,like_count,comments_count"


class InstagramConverter(DocumentConverter):
    """
    Converts a public Instagram *business/creator* profile to Markdown using the
    Instagram Graph API (Business Discovery).

    Requires credentials, supplied either as keyword arguments or environment
    variables:
        instagram_access_token / INSTAGRAM_ACCESS_TOKEN
        instagram_user_id      / INSTAGRAM_USER_ID   (the *caller's* IG user id)

    Without credentials this converter declines the input, so behavior for
    Instagram URLs is unchanged.
    """

    def accepts(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> bool:
        if _extract_username(stream_info.url) is None:
            return False
        return _credentials(kwargs) is not None

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,
    ) -> DocumentConverterResult:
        username = _extract_username(stream_info.url)
        creds = _credentials(kwargs)
        if username is None or creds is None:
            raise FileConversionException(
                "InstagramConverter needs a profile URL and API credentials."
            )
        token, user_id = creds

        limit = int(kwargs.get("instagram_media_limit", DEFAULT_MEDIA_LIMIT))
        limit = max(0, min(limit, 50))
        session = kwargs.get("requests_session") or requests

        profile = self._fetch(session, token, user_id, username, limit)
        return self._render(profile)

    def _fetch(
        self,
        session: Any,
        token: str,
        user_id: str,
        username: str,
        limit: int,
    ) -> Dict[str, Any]:
        fields = f"business_discovery.username({username}){{{_PROFILE_FIELDS}"
        if limit > 0:
            fields += f",media.limit({limit}){{{_MEDIA_FIELDS}}}"
        fields += "}"

        try:
            response = session.get(
                f"{GRAPH_API_BASE}/{GRAPH_API_VERSION}/{user_id}",
                params={"fields": fields},
                # Header, not query string, so the token never lands in URLs/logs.
                headers={"Authorization": f"Bearer {token}"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            # Do not chain: the exception text could embed request details.
            raise FileConversionException(
                f"Instagram Graph API request failed: {type(e).__name__}"
            ) from None

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code != 200 or "error" in payload:
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise FileConversionException(
                "Instagram Graph API error "
                f"(HTTP {response.status_code}, code {err.get('code')}): "
                f"{err.get('message', 'unknown error')}"
            )

        data = payload.get("business_discovery")
        if not isinstance(data, dict):
            raise FileConversionException(
                "Instagram Graph API returned no business_discovery data. "
                "The target must be a business or creator account."
            )
        return data

    def _render(self, data: Dict[str, Any]) -> DocumentConverterResult:
        username = data.get("username", "")
        name = data.get("name") or username
        md = f"# {name} (@{username})\n"

        bio = data.get("biography")
        if bio:
            md += f"\n{bio}\n"

        stats: List[str] = []
        for label, key in (
            ("Followers", "followers_count"),
            ("Following", "follows_count"),
            ("Posts", "media_count"),
        ):
            if data.get(key) is not None:
                stats.append(f"- **{label}:** {data[key]}")
        if data.get("website"):
            stats.append(f"- **Website:** {data['website']}")
        if stats:
            md += "\n## Profile\n\n" + "\n".join(stats) + "\n"

        items = (data.get("media") or {}).get("data") or []
        if items:
            md += "\n## Recent posts\n"
            for item in items:
                caption = (item.get("caption") or "").strip()
                first = caption.splitlines()[0] if caption else "(no caption)"
                link = item.get("permalink", "")
                md += (
                    f"\n### [{first[:80]}]({link})\n"
                    if link
                    else f"\n### {first[:80]}\n"
                )
                meta = [item.get("media_type"), item.get("timestamp")]
                counts = []
                if item.get("like_count") is not None:
                    counts.append(f"{item['like_count']} likes")
                if item.get("comments_count") is not None:
                    counts.append(f"{item['comments_count']} comments")
                meta_line = " · ".join(str(m) for m in meta + counts if m)
                if meta_line:
                    md += f"\n*{meta_line}*\n"
                if caption:
                    md += f"\n{caption}\n"

        return DocumentConverterResult(markdown=md, title=name)


def _extract_username(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = unquote(url)
    parsed = urlparse(url)
    # Drop query/fragment so tracking params (?igsh=...) don't defeat the match
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    m = _PROFILE_URL.match(clean)
    if not m:
        return None
    username = m.group("username")
    if username.lower() in _RESERVED_PATHS:
        return None
    return username


def _credentials(kwargs: Dict[str, Any]) -> Optional[tuple]:
    token = kwargs.get("instagram_access_token") or os.environ.get(
        "INSTAGRAM_ACCESS_TOKEN"
    )
    user_id = kwargs.get("instagram_user_id") or os.environ.get("INSTAGRAM_USER_ID")
    if token and user_id:
        return str(token), str(user_id)
    return None
