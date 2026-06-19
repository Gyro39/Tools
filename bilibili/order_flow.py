"""
抢票流程编排 —— 多阶段下单 + 智能重试。
"""
import time
import json
import random
import logging
from typing import Dict, List, Optional, Callable

from .member_buy import MemberBuyAPI

logger = logging.getLogger("bw_ticket")


class OrderFlow:
    """编排完整的抢票流程。"""

    def __init__(self, config: Dict, session, api: MemberBuyAPI):
        self.config = config
        self.session = session
        self.api = api
        self.project = config["project"]
        self.buyers = config["buyers"]
        self.advanced = config.get("advanced", {})

    @property
    def project_id(self) -> str:
        return str(self.project["project_id"])

    @property
    def screen_id(self) -> str:
        return str(self.project["screen_id"])

    @property
    def sku_id(self) -> str:
        return str(self.project["sku_id"])

    @property
    def ticket_count(self) -> int:
        return int(self.project.get("ticket_count", 1))

    @property
    def max_retries(self) -> int:
        return int(self.advanced.get("max_retries", 8))

    @property
    def retry_interval(self) -> float:
        return float(self.advanced.get("retry_interval_ms", 150)) / 1000.0

    # ---- 预检 ----

    def preflight(self) -> bool:
        checks = [
            ("登录状态", self._check_login),
            ("项目存在", self._check_project),
            ("购票人数 = 票数", self._check_buyers),
        ]
        all_ok = True
        for name, fn in checks:
            try:
                ok, msg = fn()
                status = "OK" if ok else "FAIL"
                logger.info(f"  [{status}] {name}: {msg}")
                if not ok:
                    all_ok = False
            except Exception as e:
                logger.error(f"  [FAIL] {name}: {e}")
                all_ok = False
        return all_ok

    def _check_login(self):
        ok = self.api.verify_login()
        return ok, "已登录" if ok else "未登录，请检查 Cookie"

    def _check_project(self):
        info = self.api.get_project_v2(self.project_id)
        if not info or "_error" in info:
            return False, f"无法获取项目 {self.project_id}: {info}"
        proj_name = (info.get("project_name") or info.get("name") or
                     info.get("title") or "unknown")
        return True, f"项目: {proj_name}"

    def _check_buyers(self):
        n_buyers = len(self.buyers)
        n_tickets = self.ticket_count
        if n_buyers != n_tickets:
            return False, f"购票人 ({n_buyers}) != 票数 ({n_tickets})"
        return True, f"{n_buyers} 人 / {n_tickets} 票"

    # ---- 查询项目信息 ----

    def show_project_info(self) -> Dict:
        info_v2 = self.api.get_project_v2(self.project_id)
        info_detail = self.api.get_project_detail(self.project_id)

        info = info_v2 or {}
        if isinstance(info, dict) and (info.get("_error") or not info.get("screen_list") and not info.get("screens")):
            if info_detail and not info_detail.get("_error"):
                logger.info("V2 接口无场次信息，已从 detail 接口补充")
                info = info_detail.get("data", info_detail)
                if isinstance(info, dict) and isinstance(info.get("data"), dict):
                    info = info["data"]

        if not info or "_error" in info:
            logger.error(f"获取项目信息失败: {info}")
            return {}

        err_code = info.get("errno") or info.get("err_code") or info.get("code")
        if err_code and err_code != 0:
            logger.warning(f"API 返回错误 errno={err_code} msg={info.get('msg', info.get('message', ''))}")
            logger.info("=== 原始返回 ===")
            logger.info(json.dumps(info, ensure_ascii=False, indent=2))
            return {}

        proj_name = (info.get("project_name") or info.get("name") or
                     info.get("title") or "N/A")
        logger.info(f"项目名称: {proj_name}")

        if isinstance(info, dict):
            logger.debug(f"响应顶层字段: {list(info.keys())[:20]}")

        screens = self._extract_screens(info)
        logger.info(f"场次数量: {len(screens)}")

        result = {"project_name": proj_name, "screens": [], "sale_time": ""}

        # 从场次中提取开售时间戳
        sale_ts = None
        for s in screens:
            raw_ts = s.get("sale_time") or s.get("saleTime") or s.get("start_time")
            if raw_ts and str(raw_ts).isdigit() and int(raw_ts) > 1000000000:
                sale_ts = int(raw_ts)
                break
        if sale_ts:
            from datetime import datetime as _dt
            try:
                dt = _dt.fromtimestamp(sale_ts)
                result["sale_time"] = dt.strftime("%Y-%m-%d %H:%M:%S")
                logger.info("自动获取到开售时间: {}".format(result["sale_time"]))
            except Exception:
                pass

        for s in screens:
            sid = str(s.get("screen_id") or s.get("id") or s.get("screenId") or "")
            sname = (s.get("screen_name") or s.get("name") or
                     s.get("displayName") or s.get("title") or "N/A")
            stime_value = s.get("sale_time") or s.get("saleTime") or s.get("start_time") or "N/A"

            # 场次级开售时间格式化
            screen_sale_formatted = ""
            raw_ts = s.get("sale_time") or s.get("saleTime") or s.get("start_time")
            if raw_ts and str(raw_ts).isdigit() and int(raw_ts) > 1000000000:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromtimestamp(int(raw_ts))
                    screen_sale_formatted = dt.strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    pass

            logger.info(f"  screen_id={sid}  名称={sname}  开售={stime_value}")

            skus = self._extract_skus(s)
            screen_entry = {"screen_id": sid, "screen_name": sname,
                            "skus": [], "sale_time_formatted": screen_sale_formatted}

            if skus:
                logger.debug(f"  第一个票档字段: {list(skus[0].keys())[:20]}")

            for sku in skus:
                sku_id = str(sku.get("sku_id") or sku.get("id") or sku.get("skuId") or "")
                sku_name = self._extract_sku_name(sku)
                raw_price = sku.get("price") or sku.get("sale_price") or sku.get("salePrice") or 0
                price_yuan = self._format_price(raw_price)
                logger.info(f"    sku_id={sku_id}  名称={sku_name}  价格={price_yuan}")

                # 提取票档级别的开售时间
                sku_sale_time = ""
                sku_raw_ts = (sku.get("saleStart") or sku.get("sale_start_ts") or
                               s.get("sale_time") or s.get("saleTime"))
                sku_sale_str = sku.get("sale_start") or ""
                if sku_sale_str and len(sku_sale_str) > 10:
                    sku_sale_time = sku_sale_str
                elif sku_raw_ts and str(sku_raw_ts).isdigit() and int(sku_raw_ts) > 1000000000:
                    try:
                        from datetime import datetime as _dt2
                        dt2 = _dt2.fromtimestamp(int(sku_raw_ts))
                        sku_sale_time = dt2.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
                if not sku_sale_time:
                    sku_sale_time = s.get("sale_start") or ""

                screen_entry["skus"].append({
                    "sku_id": sku_id,
                    "sku_name": sku_name,
                    "price": price_yuan,
                    "price_raw": raw_price,
                    "sold_out": ((sku.get("sale_flag") or {}).get("number") == 3 or
                                  sku.get("clickable") == False or
                                  sku.get("num") == 0 or
                                  sku.get("is_sale") == 0 or False),
                    "sale_time_formatted": sku_sale_time
                })

            # 第一个场次的开售时间作为项目默认开售时间
            if not result.get("sale_time") and screen_sale_formatted:
                result["sale_time"] = screen_sale_formatted
            result["screens"].append(screen_entry)

        if not screens:
            logger.info("未能解析出场次列表，原始返回数据：")
            self._dump_structure(info)

        return result

    def _extract_sku_name(self, sku: dict) -> str:
        """从票档对象中提取名称，尝试各种可能的字段名。"""
        for key in ("desc", "sku_name", "name", "displayName", "display_name",
                    "title", "description", "label", "tag",
                    "ticket_name", "ticketName", "show_name", "showName"):
            val = sku.get(key)
            if val and str(val).strip():
                return str(val)

        for sub_key in ("ticket_info", "ticketInfo", "base_info", "baseInfo",
                        "detail", "info", "sku_info", "skuInfo"):
            sub = sku.get(sub_key)
            if isinstance(sub, dict):
                for k in ("name", "title", "sku_name", "displayName", "desc"):
                    val = sub.get(k)
                    if val and str(val).strip():
                        return str(val)

        for k, v in sku.items():
            if isinstance(v, str) and k not in ("id", "sku_id", "skuId", "price", "type"):
                if len(v) >= 2 and not v.startswith("http"):
                    return v

        return "N/A"

    def _extract_skus(self, screen: dict) -> list:
        """从场次对象中提取票档列表。"""
        for key in ("ticket_list", "ticketList", "sku_list", "skuList",
                    "list", "tickets", "skus", "items"):
            val = screen.get(key)
            if isinstance(val, list) and len(val) > 0:
                return val
        data = screen.get("data")
        if isinstance(data, dict):
            for key in ("ticket_list", "ticketList", "sku_list", "skuList", "list"):
                val = data.get(key)
                if isinstance(val, list) and len(val) > 0:
                    return val
        return []

    def _format_price(self, raw_price) -> str:
        try:
            p = float(raw_price)
        except (ValueError, TypeError):
            return str(raw_price)
        if p > 1000:
            return f"{p / 100:.2f} 元"
        else:
            return f"{p:.2f} 元"

    def _extract_screens(self, info) -> list:
        screens = []
        if not isinstance(info, dict):
            return screens

        for key in ("screen_list", "screens", "screenList", "list"):
            val = info.get(key)
            if isinstance(val, list):
                return val

        data = info.get("data")
        if isinstance(data, dict):
            for key in ("screen_list", "screens", "screenList", "list"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
        elif isinstance(data, list):
            return data

        result_field = info.get("result")
        if isinstance(result_field, dict):
            for key in ("screen_list", "screens", "screenList", "list"):
                val = result_field.get(key)
                if isinstance(val, list):
                    return val

        for key, val in info.items():
            if isinstance(val, list) and len(val) > 0:
                first = val[0]
                if isinstance(first, dict) and any(
                    k in first for k in ("screen_id", "sku_id", "ticket_list", "ticketList")
                ):
                    screens = val
                    break
            elif isinstance(val, dict):
                for sub_key, sub_val in val.items():
                    if isinstance(sub_val, list) and len(sub_val) > 0:
                        first = sub_val[0]
                        if isinstance(first, dict) and any(
                            k in first for k in ("screen_id", "sku_id", "ticket_list", "ticketList")
                        ):
                            screens = sub_val
                            break
                if screens:
                    break
        return screens

    def _dump_structure(self, obj, prefix="", depth=0):
        if depth > 4:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    logger.info(f"  {prefix}{k}: {type(v).__name__}")
                    self._dump_structure(v, prefix + "  ", depth + 1)
                else:
                    sample = str(v)[:80]
                    logger.info(f"  {prefix}{k} = {sample}")
        elif isinstance(obj, list) and len(obj) > 0:
            logger.info(f"  {prefix}[0..{len(obj)-1}] ({len(obj)} items)")
            self._dump_structure(obj[0], prefix + "  ", depth + 1)

    # ---- 抢票主流程 ----

    def execute_grab(self) -> Dict:
        logger.info(f"start grab: project={self.project_id} "
                     f"screen={self.screen_id} sku={self.sku_id} "
                     f"amount={self.ticket_count}")

        prep_result = self._retry_phase("PREPARE", self._do_prepare)
        if not prep_result or prep_result.get("_error"):
            logger.error(f"Prepare failed: {prep_result}")
            return {"_error": "Prepare failed", "detail": prep_result}

        # Check API-level error codes (errno/err_code/code)
        api_code = prep_result.get("errno") or prep_result.get("err_code") or prep_result.get("code")
        if api_code and api_code != 0:
            api_msg = prep_result.get("msg") or prep_result.get("message") or ""
            logger.error(f"Prepare API error code={api_code} msg={api_msg}")
            return {"_error": f"API error {api_code}: {api_msg}", "detail": prep_result}

        prep_data = prep_result.get("data", prep_result)
        order_token = prep_data.get("token") or prep_data.get("order_token") or ""
        logger.info(f"Prepare OK, order_token={order_token[:20]}...")

        order_result = self._retry_phase("CREATE",
                                          lambda: self._do_create(order_token))
        if not order_result or order_result.get("_error"):
            logger.error(f"Create failed: {order_result}")
            return {"_error": "Create order failed", "detail": order_result}

        order_data = order_result.get("data", order_result)
        order_id = order_data.get("orderId") or order_data.get("order_id") or "?"
        logger.info(f"Order created! order_id={order_id}")

        return {"success": True, "order_id": order_id, "raw": order_result}

    def _do_prepare(self) -> Dict:
        return self.api.prepare_order(
            self.project_id, self.screen_id,
            self.sku_id, self.ticket_count)

    def _do_create(self, order_token: str) -> Dict:
        phone = self.buyers[0]["phone"] if self.buyers else ""
        return self.api.create_order(
            self.project_id, self.screen_id,
            self.sku_id, self.ticket_count,
            self.buyers, phone, order_token)

    def _retry_phase(self, phase_name: str, fn: Callable[[], Dict]) -> Optional[Dict]:
        last_result = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = fn()
                last_result = result
                err_code = (result.get("errno") or result.get("err_code") or result.get("code") or 0)
                err_msg = result.get("msg") or result.get("message") or ""
                if err_code == 0 and not result.get("_error"):
                    logger.debug(f"[{phase_name}] 第 {attempt} 次: OK")
                    return result
                # JSON parse error - stop retrying
                err_str = result.get("_error", "")
                if "Expecting value" in str(err_str) or "JSON" in str(err_str):
                    logger.error(f"[{phase_name}] API returned non-JSON, stopping: {err_str}")
                    return result
                if "下单过于频繁" in str(err_msg):
                    logger.warning(f"[{phase_name}] 第 {attempt} 次: 限流，重试中...")
                elif "已售罄" in str(err_msg) or "sold out" in str(err_msg).lower():
                    logger.error(f"[{phase_name}] 已售罄: {err_msg}")
                    return result
                elif "未开始" in str(err_msg) or "not started" in str(err_msg).lower():
                    logger.warning(f"[{phase_name}] 第 {attempt} 次: 尚未开售，重试中...")
                elif "已结束" in str(err_msg):
                    logger.error(f"[{phase_name}] 已结束: {err_msg}")
                    return result
                else:
                    logger.warning(f"[{phase_name}] 第 {attempt} 次: "
                                   f"err={err_code} msg={err_msg}")
            except Exception as e:
                logger.warning(f"[{phase_name}] 第 {attempt} 次: 异常={e}")
                last_result = {"_error": str(e)}
            if attempt < self.max_retries:
                delay = self.retry_interval * (1 + random.uniform(0, 1.5))
                time.sleep(delay)
        return last_result
