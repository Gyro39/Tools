"""
HTTP Session management with Bilibili cookie parsing and anti-detection headers.
"""
import requests
from typing import Dict, Optional


def parse_cookie_string(cookie_str: str) -> Dict[str, str]:
    """Parse cookie string like ""key1=val1; key2=val2"" into a dict."""
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            key, val = item.split("=", 1)
            cookies[key.strip()] = val.strip()
    return cookies


def build_session(cookie_str: str, user_agent: str,
                  proxy: str = "", timeout: int = 5) -> requests.Session:
    """Build a requests.Session with Bilibili cookies and anti-detection headers."""
    session = requests.Session()

    cookies = parse_cookie_string(cookie_str)
    # Set cookies for all relevant Bilibili domains
    for domain in ["show.bilibili.com", ".bilibili.com", "bilibili.com"]:
        for key, val in cookies.items():
            session.cookies.set(key, val, domain=domain)

    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Ch-Ua": "\"Chromium\";v=\"125\", \"Not.A/Brand\";v=\"24\"",
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": "\"Windows\"",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "X-Requested-With": "XMLHttpRequest",
        "TE": "trailers",
    })

    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    session.timeout = timeout
    return session


def get_csrf_token(session) -> str:
    """Extract bili_jct (CSRF token) from session cookies."""
    for cookie in session.cookies:
        if cookie.name == "bili_jct":
            return cookie.value
    return ""


def get_sessdata(session) -> str:
    """Extract SESSDATA from session cookies."""
    for cookie in session.cookies:
        if cookie.name == "SESSDATA":
            return cookie.value
    return ""


def validate_session(session: requests.Session) -> Dict[str, bool]:
    """Validate that required cookies are present."""
    return {
        "SESSDATA": bool(get_sessdata(session)),
        "bili_jct": bool(get_csrf_token(session)),
    }
