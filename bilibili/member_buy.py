"""
会员购 (show.bilibili.com) API 客户端。
项目信息 / 下单 / 查单。
"""
import json
import time
import random
import logging
import requests
from typing import Dict, List, Optional, Any
from urllib.parse import urljoin
try:
    import brotli
except ImportError:
    import subprocess, sys
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "brotli", "--quiet"])
        import brotli
    except Exception:
        brotli = None

SHOW_BASE = "https://show.bilibili.com/api/ticket/"

logger = logging.getLogger("bw_ticket")


class MemberBuyAPI:
    """Bilibili 会员购 (show.bilibili.com) 票务 API 客户端。"""

    def __init__(self, session: requests.Session):
        self.session = session
        self.csrf = self._extract_csrf()

    def _extract_csrf(self) -> str:
        for cookie in self.session.cookies:
            if cookie.name == "bili_jct":
                return cookie.value
        return ""

    def _url(self, path: str) -> str:
        return urljoin(SHOW_BASE, path)

    def _jitter(self, base_s: float = 0.0, spread: float = 0.05) -> None:
        time.sleep(base_s + random.uniform(0, spread))

    def _decompress(self, resp):
        if resp.headers.get("content-encoding") == "br":
            if brotli is None:
                logger.error("Response is Brotli-compressed but brotli not installed. Run: pip install brotli")
                return resp.content
            try:
                return brotli.decompress(resp.content)
            except Exception:
                pass
        return resp.content

    def _headers(self, referer: str = "https://show.bilibili.com/") -> Dict:
        return {
            "Referer": referer,
            "Origin": "https://show.bilibili.com",
        }

    # ---- 项目信息 ----

    def get_project_v2(self, project_id: str) -> Optional[Dict]:
        """获取项目基本信息（场次、票档、开售时间）。"""
        urls = [
            f"project/getV2?version=134&id={project_id}",
            f"project/getV2?id={project_id}",
        ]
        last_error = None

        for path in urls:
            url = self._url(path)
            try:
                resp = self.session.get(url, headers=self._headers(), timeout=10)
                logger.debug(f"[API] GET {url} -> HTTP {resp.status_code}")
                try:
                    data = resp.json()
                except Exception:
                    logger.debug(f"非 JSON 响应 (前200字符): {resp.text[:200]}")
                    continue

                # B站会员购有两种返回格式：
                # 格式A: {errno: 0, data: {...}}
                # 格式B: {success: true, code: 0, data: {...}}
                is_ok = (
                    data.get("errno") == 0 or
                    data.get("err_code") == 0 or
                    (data.get("success") and data.get("code") == 0)
                )
                if is_ok:
                    inner = data.get("data", data)
                    # 有时候 data 里还包了一层 data
                    if isinstance(inner, dict) and "data" in inner and isinstance(inner["data"], dict):
                        inner = inner["data"]
                    return inner

                err_code = data.get("errno") or data.get("err_code") or data.get("code")
                err_msg = data.get("msg") or data.get("message") or ""
                logger.debug(f"[API] 返回 errno={err_code} msg={err_msg}")
                return data

            except Exception as e:
                last_error = str(e)
                logger.debug(f"[API] 请求失败: {e}")

        return {"_error": last_error or "所有接口均无响应"}

    def get_project_detail(self, project_id: str) -> Optional[Dict]:
        """获取项目详细信息。"""
        url = self._url(f"project/detail?project_id={project_id}")
        try:
            resp = self.session.get(url, headers=self._headers(), timeout=10)
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    # ---- 购票人 ----

    def get_buyer_list(self) -> Optional[Dict]:
        """获取账号保存的实名购票人列表。"""
        url = self._url("buyer/list")
        try:
            resp = self.session.get(
                url,
                headers=self._headers("https://show.bilibili.com/platform/home.html"),
                timeout=10
            )
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    # ---- 下单流程 ----

    def prepare_order(self, project_id: str, screen_id: str,
                      sku_id: str, amount: int = 1) -> Optional[Dict]:
        """Step 1: 下单预备，获取 order_token。"""
        url = self._url("order/prepare")
        payload = {
            "project_id": str(project_id),
            "screen_id": str(screen_id),
            "sku_id": str(sku_id),
            "amount": str(amount),
            "token": self.csrf,
            "requestSource": "pc-host",
            "coupon_token": "",
            "coupon_id": "",
            "account_id": "",
            "buyer": "",
        }
        logger.debug(f"[PREPARE] payload: {payload}")
        try:
            h = self._headers(f"https://show.bilibili.com/platform/detail.html?id={project_id}")
            h["x-csrf-token"] = self.csrf
            pass  # Cookie set by session
            resp = self.session.post(
                url, data=payload,
                headers=h,
                timeout=5
            )
            logger.debug(f"[PREPARE] HTTP {resp.status_code} len={len(resp.text)} content-type={resp.headers.get('content-type', 'N/A')}")
            if resp.status_code != 200:
                logger.error(f"[PREPARE] HTTP error {resp.status_code}: {resp.text[:500]}")
            if not resp.text.strip():
                logger.error(f"[PREPARE] Empty response! Headers: {dict(resp.headers)}")
            logger.error(f"[PREPARE] Raw body: {resp.content[:200].hex()}")
            logger.error(f"[PREPARE] Content-Encoding: {resp.headers.get("content-encoding", "none")}")
            logger.error(f"[PREPARE] Status: {resp.status_code}")
            try:
                import gzip; raw_body = gzip.decompress(resp.content); logger.error(f"[PREPARE] Gzip decompressed: {raw_body[:200]}")
            except Exception:
                pass
            logger.error(f"[PREPARE] Raw bytes (hex): {resp.content[:100].hex()}")
            try:
                decompressed = self._decompress(resp)
                return json.loads(decompressed)
            except Exception:
                pass
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    def create_order(self, project_id: str, screen_id: str,
                     sku_id: str, amount: int,
                     buyers: List[Dict],
                     phone: str,
                     prepare_token: str = "") -> Optional[Dict]:
        """Step 2: 创建真实订单。"""
        url = self._url("order/createV2")

        buyer_info = []
        for b in buyers:
            buyer_info.append({
                "name": b["name"],
                "personal_id": b["id_number"],
                "id_type": b.get("id_type", 0),
                "isBuyerInfoVerified": True,
            })

        payload = {
            "project_id": str(project_id),
            "screen_id": str(screen_id),
            "sku_id": str(sku_id),
            "amount": str(amount),
            "phone": phone,
            "buyer_info": buyer_info,
            "pay_money": "",
            "token": self.csrf,
            "coupon_token": "",
            "coupon_id": "",
            "requestSource": "pc-host",
            "delivery_type": 0,
            "account_id": "",
            "buyer": "",
            "is_use_mit": 0,
        }
        if prepare_token:
            payload["order_token"] = prepare_token

        try:
            resp = self.session.post(
                url,
                data={"requestData": json.dumps(payload, ensure_ascii=False)},
                headers=self._headers(f"https://show.bilibili.com/platform/detail.html?id={project_id}"),
                timeout=5
            )
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    def get_order_info(self, order_id: str) -> Optional[Dict]:
        """查询订单状态。"""
        url = self._url(f"order/orderInfo?order_id={order_id}")
        try:
            resp = self.session.get(
                url,
                headers=self._headers("https://show.bilibili.com/platform/order.html"),
                timeout=5
            )
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    # ---- 工具 ----

    def verify_login(self) -> bool:
        """检查当前会话是否已登录。"""
        try:
            resp = self.session.get(
                "https://api.bilibili.com/x/web-interface/nav", timeout=5)
            data = resp.json()
            return data.get("data", {}).get("isLogin", False)
        except Exception:
            return False

    def get_sale_timestamp(self, project_id: str) -> Optional[float]:
        """获取项目的开售时间戳（秒）。"""
        info = self.get_project_v2(project_id)
        if not info or "_error" in info:
            return None

        sale_time = (info.get("sale_time") or info.get("start_time") or
                     info.get("start_sale_time") or info.get("sale_flag_time") or 0)

        if isinstance(sale_time, str):
            from datetime import datetime
            try:
                dt = datetime.strptime(sale_time, "%Y-%m-%d %H:%M:%S")
                return dt.timestamp()
            except ValueError:
                pass

        if sale_time and sale_time > 1000000000:
            return float(sale_time)

        for screen in info.get("screen_list", info.get("screens", [])):
            st = screen.get("sale_time") or screen.get("start_time") or 0
            if st and st > 1000000000:
                return float(st)

        return None
