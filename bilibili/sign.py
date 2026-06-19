"""
Wbi signing implementation for Bilibili API.
Reference: https://github.com/SocialSisterYi/bilibili-API-collect
"""
import hashlib
import time
from typing import Dict, Tuple
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
import requests


MIXIN_KEY_ENC_TABLE = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 52, 44, 34
]


def get_mixin_key(raw_key: str) -> str:
    """Apply the mixin transform to derive the real signing key."""
    return "".join(raw_key[i] for i in MIXIN_KEY_ENC_TABLE if i < len(raw_key))


class WbiSigner:
    """Handles Wbi signing for api.bilibili.com endpoints."""

    CACHE_TTL = 3600  # 1 hour

    def __init__(self, session: requests.Session):
        self.session = session
        self._mixin_key = ""
        self._cached_at = 0.0

    def _fetch_keys(self) -> Tuple[str, str]:
        """Fetch img_key and sub_key from Bilibili nav endpoint."""
        try:
            resp = self.session.get(
                "https://api.bilibili.com/x/web-interface/nav", timeout=5)
            data = resp.json()
            wbi_img = data.get("data", {}).get("wbi_img", {})
            img_url = wbi_img.get("img_url", "")
            sub_url = wbi_img.get("sub_url", "")
            img_key = img_url.rsplit("/", 1)[-1].split(".")[0] if img_url else ""
            sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0] if sub_url else ""
            if img_key and sub_key:
                return img_key, sub_key
        except Exception:
            pass
        # Fallback static keys
        return (
            "7cd084941338484aae1ad9425b84077c",
            "4932caff0ff746eab6f01bf08b70ac45"
        )

    def _ensure_keys(self):
        now = time.time()
        if not self._mixin_key or (now - self._cached_at) > self.CACHE_TTL:
            img_key, sub_key = self._fetch_keys()
            raw = img_key[:8] + sub_key[2:10]
            self._mixin_key = get_mixin_key(raw)
            self._cached_at = now

    def sign_params(self, params: Dict[str, str]) -> Dict[str, str]:
        """Add w_rid and wts signature to a params dict."""
        self._ensure_keys()
        params = {k: v for k, v in params.items()}
        params.pop("w_rid", None)
        params.pop("wts", None)
        wts = str(int(time.time()))
        params["wts"] = wts
        sorted_items = sorted(params.items(), key=lambda x: x[0])
        query = urlencode(sorted_items)
        sign_str = query + self._mixin_key
        w_rid = hashlib.md5(sign_str.encode()).hexdigest()
        params["w_rid"] = w_rid
        return params

    def sign_url(self, url: str) -> str:
        """Sign a URL by appending w_rid and wts query params."""
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        signed = self.sign_params(params)
        return urlunparse(parsed._replace(query=urlencode(signed)))
