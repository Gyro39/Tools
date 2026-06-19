# -*- coding: utf-8 -*-
"""
BW 抢票助手 GUI 启动器
双击启动，可视化配置，一键抢票。
"""
import json
import sys
import os
import queue
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

ROOT = Path(__file__).parent
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

from bilibili.session import build_session, validate_session
from bilibili.member_buy import MemberBuyAPI
from bilibili.order_flow import OrderFlow
from scheduler import PrecisionScheduler

CONFIG_PATH = ROOT / "config.json"
EXAMPLE_PATH = ROOT / "config.example.json"

ID_TYPE_MAP = {"身份证": 0, "护照": 1, "港澳通行证": 2, "台胞证": 3}
ID_TYPE_REVERSE = {0: "身份证", 1: "护照", 2: "港澳通行证", 3: "台胞证"}


class GuiLogHandler:
    def __init__(self, log_queue):
        self.log_queue = log_queue

    def write(self, text):
        if text.strip():
            self.log_queue.put(("log", text.rstrip()))

    def flush(self):
        pass


def make_gui_logger(log_queue):
    import logging
    logger = logging.getLogger("bw_ticket")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(GuiLogHandler(log_queue))
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] %(levelname)-5s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)
    return logger


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    if EXAMPLE_PATH.exists():
        with open(EXAMPLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return _default_config()


def _default_config():
    return {
        "project": {"project_id": "", "screen_id": "", "sku_id": "", "ticket_count": 1},
        "buyers": [],
        "account": {"cookie_string": ""},
        "schedule": {"sale_time": "", "advance_ms": 300},
        "advanced": {
            "max_retries": 8, "retry_interval_ms": 150,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "proxy": "", "timeout_s": 5
        }
    }


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("BW 抢票助手")
        self.root.geometry("840x780")
        self.root.minsize(700, 640)

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.running = False

        # 下拉框映射：显示文本 -> ID
        self.screen_options = {}   # "329309 - 2026/06/20" -> "329309"
        self.sku_options = {}      # "842854 - 普通票 - 65.00元" -> "842854"

        self.cfg = load_config()
        self.all_screens_data = []  # 完整场次数据，用于切换联动
        self._scheduler = None  # 调度器引用，用于停止
        self._build_ui()
        self._load_to_ui()
        self._poll_logs()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ===== UI 构建 =====
    def _build_ui(self):
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        self.notebook.add(self._make_account_tab(), text="  账号  ")
        self.notebook.add(self._make_project_tab(), text="  项目  ")
        self.notebook.add(self._make_buyers_tab(),  text="  购票人  ")
        self.notebook.add(self._make_schedule_tab(),text="  开票时间  ")
        self.notebook.add(self._make_advanced_tab(),text="  高级设置  ")
        self.notebook.add(self._make_guide_tab(),     text="  使用说明  ")

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", padx=8, pady=4)
        ttk.Button(btn_frame, text="保存配置", command=self._save).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="检查配置", command=self._check).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="查询项目", command=self._info).pack(side="left", padx=2)
        ttk.Button(btn_frame, text="模拟倒计时", command=self._monitor).pack(side="left", padx=2)
        self.btn_grab = ttk.Button(btn_frame, text="开始抢票", command=self._grab)
        self.btn_grab.pack(side="left", padx=2)
        ttk.Button(btn_frame, text="停止", command=self._stop).pack(side="left", padx=2)

        log_frame = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=4)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.log_text = scrolledtext.ScrolledText(
            log_frame, wrap="word", state="disabled",
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        self.log_text.pack(fill="both", expand=True)

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self.root, textvariable=self.status_var,
                  relief="sunken", anchor="w", padding=(6, 2)).pack(fill="x", side="bottom")

    # ===== 各标签页 =====
    def _make_account_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        ttk.Label(f, text="Cookie 字符串", font=("", 10, "bold")).pack(anchor="w")
        ttk.Label(f, text="登录 bilibili.com，打开抢票页面 -> F12 -> 网络(Network) -> 点列表第一行 -> 标头(Headers) -> 复制 Cookie 整行到下方：",
                  foreground="gray").pack(anchor="w")
        self.cookie_text = tk.Text(f, height=6, font=("Consolas", 9))
        self.cookie_text.pack(fill="both", expand=True, pady=(4, 8))
        ttk.Label(f, text="提示：从请求标头复制的 Cookie 行直接整行粘贴即可，无需手动提取字段",
                  foreground="gray").pack(anchor="w")
        return f

    def _make_project_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        ttk.Label(f, text="项目信息", font=("", 10, "bold")).pack(anchor="w", pady=(0, 8))

        # project_id (手动填)
        r1 = ttk.Frame(f); r1.pack(fill="x", pady=4)
        ttk.Label(r1, text="项目编号", width=16).pack(side="left")
        self.proj_id = ttk.Entry(r1)
        self.proj_id.pack(side="left", fill="x", expand=True)
        ttk.Label(r1, text="BW 购票页 URL 中的数字", foreground="gray").pack(side="left", padx=4)

        # screen_id 下拉框
        r2 = ttk.Frame(f); r2.pack(fill="x", pady=4)
        ttk.Label(r2, text="场次选择", width=16).pack(side="left")
        self.screen_var = tk.StringVar()
        self.proj_screen = ttk.Combobox(r2, textvariable=self.screen_var,
                                         state="readonly", width=50)
        self.proj_screen.pack(side="left", fill="x", expand=True)
        ttk.Label(r2, text="点「查询项目」自动获取", foreground="gray").pack(side="left", padx=4)

        # sku_id 下拉框
        r3 = ttk.Frame(f); r3.pack(fill="x", pady=4)
        ttk.Label(r3, text="票档选择", width=16).pack(side="left")
        self.sku_var = tk.StringVar()
        self.proj_sku = ttk.Combobox(r3, textvariable=self.sku_var,
                                      state="readonly", width=50)
        self.proj_sku.pack(side="left", fill="x", expand=True)
        ttk.Label(r3, text="点「查询项目」自动获取", foreground="gray").pack(side="left", padx=4)

        # 票数
        r4 = ttk.Frame(f); r4.pack(fill="x", pady=4)
        ttk.Label(r4, text="购票数量", width=16).pack(side="left")
        self.proj_count = ttk.Spinbox(r4, from_=1, to=10, width=8)
        self.proj_count.pack(side="left")
        ttk.Label(r4, text="张（必须与购票人数一致）", foreground="gray").pack(side="left", padx=4)

        ttk.Label(f, text="提示：填入 project_id -> 保存配置 -> 点「查询项目」，"
                  "场次和票档会自动填入上方下拉框",
                  foreground="gray").pack(anchor="w", pady=(12, 0))
        return f

    def _make_buyers_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        ttk.Label(f, text="购票人信息", font=("", 10, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(f, text="购票人数必须与「项目」标签中的数量一致，信息务必准确",
                  foreground="gray").pack(anchor="w", pady=(0, 8))

        btn_bar = ttk.Frame(f); btn_bar.pack(fill="x", pady=(0, 4))
        ttk.Button(btn_bar, text="+ 添加购票人", command=self._add_buyer_row).pack(side="left", padx=2)
        ttk.Button(btn_bar, text="- 删除最后一人", command=self._remove_buyer_row).pack(side="left", padx=2)

        header = ttk.Frame(f); header.pack(fill="x")
        for col, w in [("姓名", 13), ("证件类型", 12), ("证件号码", 25), ("手机号", 16)]:
            ttk.Label(header, text=col, width=w, anchor="center",
                      font=("", 9, "bold")).pack(side="left", padx=1)

        cf = ttk.Frame(f); cf.pack(fill="both", expand=True)
        self.buyer_canvas = tk.Canvas(cf, height=220, highlightthickness=0)
        sb = ttk.Scrollbar(cf, orient="vertical", command=self.buyer_canvas.yview)
        self.buyer_rows_frame = ttk.Frame(self.buyer_canvas)
        self.buyer_rows_frame.bind("<Configure>",
            lambda e: self.buyer_canvas.configure(scrollregion=self.buyer_canvas.bbox("all")))
        self.buyer_canvas.create_window((0, 0), window=self.buyer_rows_frame, anchor="nw")
        self.buyer_canvas.configure(yscrollcommand=sb.set)
        self.buyer_canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.buyer_rows = []
        return f

    def _make_schedule_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        ttk.Label(f, text="开票时间设置", font=("", 10, "bold")).pack(anchor="w", pady=(0, 8))

        r1 = ttk.Frame(f); r1.pack(fill="x", pady=6)
        ttk.Label(r1, text="开票日期", width=16).pack(side="left")
        self.sched_date = ttk.Entry(r1, width=14); self.sched_date.pack(side="left", padx=4)
        ttk.Label(r1, text="格式：YYYY-MM-DD  例如 2026-06-28", foreground="gray").pack(side="left", padx=4)

        r2 = ttk.Frame(f); r2.pack(fill="x", pady=6)
        ttk.Label(r2, text="开票时间", width=16).pack(side="left")
        self.sched_time = ttk.Entry(r2, width=14); self.sched_time.pack(side="left", padx=4)
        ttk.Label(r2, text="格式：HH:MM:SS  例如 20:00:00", foreground="gray").pack(side="left", padx=4)

        ttk.Separator(f, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(f, text="提前触发时间", font=("", 10, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(f, text="脚本会在开票时间之前提前发起请求，以抵消网络延迟。",
                  foreground="gray").pack(anchor="w")

        r3 = ttk.Frame(f); r3.pack(fill="x", pady=6)
        ttk.Label(r3, text="提前量", width=16).pack(side="left")
        self.sched_advance = ttk.Spinbox(r3, from_=0, to=2000, increment=50, width=8)
        self.sched_advance.pack(side="left", padx=4)
        ttk.Label(r3, text="毫秒（建议 200-500ms）", foreground="gray").pack(side="left", padx=4)
        ttk.Label(f, text="注意：提前量太大容易被风控拦截，太小可能抢不到",
                  foreground="gray").pack(anchor="w", pady=(6, 0))
        return f

    def _make_guide_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        t = scrolledtext.ScrolledText(f, wrap="word", font=("Microsoft YaHei", 10),
                                       bg="#fafafa", padx=10, pady=8, height=22)
        t.pack(fill="both", expand=True)
        t.insert("1.0",
            "============================================================\n"
            "                    BW 抢票助手 使用说明\n"
            "============================================================\n"
            "\n"
            "一、操作流程\n"
            "  1. [账号] 粘贴 Cookie\n"
            "  2. [项目] 填入项目编号 -> 保存配置 -> 点查询项目\n"
            "     场次和票档会自动填入下拉框，选好想要的场次\n"
            "     开票日期和时间会自动联动更新\n"
            "  3. [购票人] 添加购票人（人数 = 票数）\n"
            "  4. [开票时间] 确认时间和提前量（建议 200-500ms）\n"
            "  5. 点检查配置确认一切就绪\n"
            "  6. 点开始抢票，脚本开始倒计时等待\n"
            "\n"
            "二、按钮说明\n"
            "  保存配置   - 将界面数据写入 config.json\n"
            "  检查配置   - 验证 Cookie 有效性 + 配置完整性\n"
            "  查询项目   - 拉取场次或票档或开售时间并自动填入\n"
            "  模拟倒计时 - 只跑倒计时，不实际下单，用于测试时机\n"
            "  开始抢票   - 正式抢票！到点自动发请求\n"
            "  停止       - 中断正在运行的任务\n"
            "\n"
            "三、重要提示\n"
            "  * 点开始抢票后窗口必须一直开着！\n"
            "    关了窗口 = 杀掉进程 = 不会自动抢\n"
            "  * 建议开票前 3-5 分钟打开脚本，先点检查配置\n"
            "    确认 Cookie 有效，然后立刻点开始抢票\n"
            "  * 如果你的电脑长时间不用可能休眠，请提前关闭休眠\n"
            "  * Cookie 可能几小时后过期，不要提前太久开脚本\n"
            "  * 正式抢票前建议先跑一遍模拟倒计时测试时机\n"
            "  * 抢票成功后会显示订单号，需尽快去 B 站完成支付\n"
            "  * 所有操作日志保存在 logs/ 目录下\n"
            "\n"
            "四、Cookie 获取方法\n"
            "  1. Chrome 打开 bilibili.com 并登录\n"
            "  2. 打开 BW 抢票页面\n"
            "  3. 按 F12 -> 点击网络 (Network) 标签\n"
            "  4. 按 F5 刷新页面\n"
            "  5. 左侧列表中点击第一行\n"
            "  6. 右侧找到标头 (Headers) -> 请求标头 -> Cookie\n"
            "  7. 复制 Cookie 后面的整行，粘贴到账号标签\n"
        )
        t.configure(state="disabled")
        return f



    def _make_advanced_tab(self):
        f = ttk.Frame(self.notebook, padding=12)
        ttk.Label(f, text="高级设置", font=("", 10, "bold")).pack(anchor="w", pady=(0, 8))
        ttk.Label(f, text="以下参数一般不需要修改，保持默认即可",
                  foreground="gray").pack(anchor="w", pady=(0, 8))

        def row(label, widget, hint=""):
            r = ttk.Frame(f); r.pack(fill="x", pady=4)
            ttk.Label(r, text=label, width=18).pack(side="left")
            widget.pack(side="left", fill="x", expand=True, padx=4)
            if hint:
                ttk.Label(r, text=hint, foreground="gray").pack(side="left")

        self.adv_retries = ttk.Spinbox(f, from_=1, to=50, width=8)
        row("最大重试次数", self.adv_retries, "次")

        self.adv_interval = ttk.Spinbox(f, from_=50, to=2000, increment=50, width=8)
        row("重试间隔", self.adv_interval, "毫秒")

        self.adv_timeout = ttk.Spinbox(f, from_=1, to=30, width=8)
        row("请求超时", self.adv_timeout, "秒")

        self.adv_ua = ttk.Entry(f)
        row("User-Agent", self.adv_ua)

        self.adv_proxy = ttk.Entry(f)
        row("代理地址", self.adv_proxy, "例如 http://127.0.0.1:8888（留空则不使用代理）")
        return f

    # ===== 数据同步 =====
    def _load_to_ui(self):
        c = self.cfg
        self.cookie_text.delete("1.0", "end")
        self.cookie_text.insert("1.0", c["account"].get("cookie_string", ""))

        p = c["project"]
        self.proj_id.delete(0, "end"); self.proj_id.insert(0, str(p.get("project_id", "")))
        self.proj_count.delete(0, "end"); self.proj_count.insert(0, str(p.get("ticket_count", 1)))

        # 下拉框：如果有映射就用映射，没有就填原始 ID
        saved_screen = str(p.get("screen_id", ""))
        saved_sku = str(p.get("sku_id", ""))
        self.screen_var.set(self.screen_options.get(saved_screen, saved_screen))
        self.sku_var.set(self.sku_options.get(saved_sku, saved_sku))

        for _ in range(len(self.buyer_rows)):
            self._remove_buyer_row_real()
        for b in c.get("buyers", []):
            self._add_buyer_row(b)

        s = c["schedule"]
        sale = s.get("sale_time", "")
        if " " in sale:
            date_part, time_part = sale.split(" ", 1)
        else:
            date_part, time_part = "", ""
        self.sched_date.delete(0, "end"); self.sched_date.insert(0, date_part)
        self.sched_time.delete(0, "end"); self.sched_time.insert(0, time_part)
        self.sched_advance.delete(0, "end"); self.sched_advance.insert(0, str(s.get("advance_ms", 300)))

        a = c["advanced"]
        self.adv_ua.delete(0, "end"); self.adv_ua.insert(0, a.get("user_agent", ""))
        self.adv_retries.delete(0, "end"); self.adv_retries.insert(0, str(a.get("max_retries", 8)))
        self.adv_interval.delete(0, "end"); self.adv_interval.insert(0, str(a.get("retry_interval_ms", 150)))
        self.adv_timeout.delete(0, "end"); self.adv_timeout.insert(0, str(a.get("timeout_s", 5)))
        self.adv_proxy.delete(0, "end"); self.adv_proxy.insert(0, a.get("proxy", ""))

    def _collect_from_ui(self):
        buyers = []
        for rd in self.buyer_rows:
            try:
                id_type_display = rd["id_type"].get()
                buyers.append({
                    "name": rd["name"].get(),
                    "id_type": ID_TYPE_MAP.get(id_type_display, 0),
                    "id_number": rd["id_number"].get(),
                    "phone": rd["phone"].get(),
                })
            except (ValueError, tk.TclError):
                pass

        # 从下拉框显示文本中提取真实 ID
        screen_display = self.screen_var.get()
        screen_id = self.screen_options.get(screen_display, screen_display)
        sku_display = self.sku_var.get()
        sku_id = self.sku_options.get(sku_display, sku_display)

        sale_time = f"{self.sched_date.get().strip()} {self.sched_time.get().strip()}"
        return {
            "project": {
                "project_id": self.proj_id.get().strip(),
                "screen_id": screen_id.strip(),
                "sku_id": sku_id.strip(),
                "ticket_count": int(self.proj_count.get() or 1),
            },
            "buyers": buyers,
            "account": {"cookie_string": self.cookie_text.get("1.0", "end-1c").strip()},
            "schedule": {"sale_time": sale_time.strip(),
                          "advance_ms": int(self.sched_advance.get() or 300)},
            "advanced": {
                "max_retries": int(self.adv_retries.get() or 8),
                "retry_interval_ms": int(self.adv_interval.get() or 150),
                "user_agent": self.adv_ua.get().strip(),
                "proxy": self.adv_proxy.get().strip(),
                "timeout_s": int(self.adv_timeout.get() or 5),
            }
        }

    # ===== 购票人列表 =====
    def _add_buyer_row(self, data=None):
        data = data or {}
        row = ttk.Frame(self.buyer_rows_frame); row.pack(fill="x", pady=1)
        e_name = ttk.Entry(row, width=14); e_name.pack(side="left", padx=1)
        e_name.insert(0, data.get("name", ""))

        id_display = ID_TYPE_REVERSE.get(data.get("id_type", 0), "身份证")
        id_type_var = tk.StringVar(value=id_display)
        cb = ttk.Combobox(row, textvariable=id_type_var,
                           values=["身份证", "护照", "港澳通行证", "台胞证"],
                           width=12, state="readonly")
        cb.pack(side="left", padx=1)

        e_id = ttk.Entry(row, width=26); e_id.pack(side="left", padx=1)
        e_id.insert(0, data.get("id_number", ""))
        e_phone = ttk.Entry(row, width=17); e_phone.pack(side="left", padx=1)
        e_phone.insert(0, data.get("phone", ""))

        self.buyer_rows.append({
            "frame": row, "name": e_name, "id_type": id_type_var,
            "id_number": e_id, "phone": e_phone
        })

    def _remove_buyer_row(self):
        if self.buyer_rows:
            self._remove_buyer_row_real()

    def _remove_buyer_row_real(self):
        self.buyer_rows.pop()["frame"].destroy()

    # ===== 日志 & 自动填入 =====
    def _append_log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _on_screen_changed(self, event=None):
        """场次下拉框切换时，联动更新开票时间和票档下拉框。"""
        selected = self.screen_var.get()
        screen_id = self.screen_options.get(selected, "")
        if not screen_id or not self.all_screens_data:
            return

        # 更新开票日期 + 时间
        matched = None
        for s in self.all_screens_data:
            if s.get("screen_id") == screen_id:
                matched = s
                break
        if not matched:
            return

        # 更新开票日期 + 时间
        sale_time = matched.get("sale_time_formatted", "")
        if sale_time and " " in sale_time:
            date_part, time_part = sale_time.split(" ", 1)
            self.sched_date.delete(0, "end")
            self.sched_date.insert(0, date_part)
            self.sched_time.delete(0, "end")
            self.sched_time.insert(0, time_part)
            self._append_log(">>> 已更新开票时间: " + sale_time)

        # 更新票档下拉框
        sku_choices = []
        self.sku_options = {}
        for sku in matched.get("skus", []):
            sku_id = sku.get("sku_id", "")
            sku_name = sku.get("sku_name", "N/A")
            price = sku.get("price", "?")
            sold_out = sku.get("sold_out", False)
            label = "{}  |  {}  |  {}".format(sku_id, sku_name, price)
            if sold_out:
                label = "[已停售] " + label
            sku_choices.append(label)
            self.sku_options[label] = sku_id

        self.proj_sku["values"] = sku_choices
        if sku_choices:
            self.proj_sku.current(0)
            self._append_log(">>> 已更新 {} 个票档（{}）".format(len(sku_choices), matched.get("screen_name", "")))
            self.proj_sku.bind("<<ComboboxSelected>>", self._on_sku_changed)
            self._on_sku_changed()


    def _on_sku_changed(self, event=None):
        """SKU change handler."""
        self._append_log("DEBUG _on_sku_changed called")
        selected = self.sku_var.get()
        sku_id = self.sku_options.get(selected, "")
        self._append_log("DEBUG sku selected=" + selected[:50] + " id=" + sku_id)
        if not sku_id or not self.all_screens_data:
            self._append_log("DEBUG early return: sku_id=" + repr(sku_id) + " data_len=" + str(len(self.all_screens_data)))
            return
        screen_sel = self.screen_var.get()
        screen_id = self.screen_options.get(screen_sel, "")
        self._append_log("DEBUG screen=" + screen_id + " screens_count=" + str(len(self.all_screens_data)))
        for s in self.all_screens_data:
            if s.get("screen_id") == screen_id:
                self._append_log("DEBUG found screen, skus=" + str(len(s.get("skus", []))))
                for sku in s.get("skus", []):
                    if sku.get("sku_id") == sku_id:
                        sale_time = sku.get("sale_time_formatted", "") or sku.get("sale_start", "")
                        self._append_log("DEBUG sale_time_formatted=" + repr(sku.get("sale_time_formatted")) + " sale_start=" + repr(sku.get("sale_start")))
                        if not sale_time:
                            sale_time = s.get("sale_time_formatted", "")
                        if sale_time and " " in sale_time:
                            date_part, time_part = sale_time.split(" ", 1)
                            self.sched_date.delete(0, "end")
                            self.sched_date.insert(0, date_part)
                            self.sched_time.delete(0, "end")
                            self.sched_time.insert(0, time_part)
                            self._append_log(">>> SKU sale time: " + sale_time)
                        else:
                            self._append_log("DEBUG sale_time empty or no space: " + repr(sale_time))
                        return
                self._append_log("DEBUG sku_id not found in screen skus")
                break


    def _auto_fill_project(self, data):
        """将查到的场次和票档填入下拉框，切换场次时自动联动开票时间。"""
        screens = data.get("screens", [])
        if not screens:
            return

        # 更新开票日期 + 时间?????
        self.all_screens_data = screens

        # 更新票档下拉框?
        screen_choices = []
        self.screen_options = {}
        for s in screens:
            sid = s.get("screen_id", "")
            sname = s.get("screen_name", "N/A")
            label = "{}  |  {}".format(sid, sname)
            screen_choices.append(label)
            self.screen_options[label] = sid

        self.proj_screen["values"] = screen_choices
        if screen_choices:
            self.proj_screen.current(0)
            self._append_log(">>> 已加载 {} 个场次到下拉框".format(len(screen_choices)))

        # 更新开票日期 + 时间?????
        self.proj_screen.bind("<<ComboboxSelected>>", self._on_screen_changed)

        # 更新开票日期 + 时间??????????
        self._on_screen_changed()
        self._append_log("切换场次后开票时间和票档会自动更新。如需修改，直接在下拉框里选即可。")

    def _poll_logs(self):
        try:
            while True:
                kind, msg = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(msg)
                elif kind == "status":
                    self.status_var.set(msg)
                elif kind == "autofill":
                    self._auto_fill_project(msg)
                elif kind == "done":
                    self._on_task_done()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_logs)

    def _on_task_done(self):
        self.running = False
        self.btn_grab.configure(state="normal")
        self._append_log("--- 任务结束 ---")

    def _run_in_thread(self, target, name=""):
        if self.running:
            messagebox.showwarning("操作冲突", "当前有任务正在运行，请先点「停止」")
            return
        self.running = True
        self.status_var.set(f"运行中：{name}")
        self.btn_grab.configure(state="disabled")
        self._append_log(f"{'=' * 50}")
        self._append_log(f"开始：{name}")
        self._append_log(f"{'=' * 50}")
        self.worker_thread = threading.Thread(target=target, daemon=True)
        self.worker_thread.start()

    # ===== 操作 =====
    def _save(self):
        self.cfg = self._collect_from_ui()
        save_config(self.cfg)
        self._append_log("[OK] 配置已保存到 config.json")
        self.status_var.set("配置已保存")
        messagebox.showinfo("保存成功", "配置已保存到 config.json")

    def _check(self):
        cfg = self._collect_from_ui()
        save_config(cfg)
        issues = []
        proj = cfg["project"]
        for key in ["project_id", "screen_id", "sku_id"]:
            if not proj.get(key):
                issues.append(f"项目 {key} 未填写")
        if not cfg["buyers"]:
            issues.append("未添加购票人")
        else:
            count = int(proj.get("ticket_count", 0))
            if len(cfg["buyers"]) != count:
                issues.append(f"购票人数 ({len(cfg['buyers'])}) 与购票数量 ({count}) 不一致")
        if not cfg["account"].get("cookie_string"):
            issues.append("Cookie 未填写")
        if not cfg["schedule"].get("sale_time", "").strip():
            issues.append("开票时间未填写")
        if issues:
            self._append_log("[FAIL] 配置存在以下问题：")
            for i in issues:
                self._append_log(f"  - {i}")
            return
        self._append_log("[*] 正在检查登录状态...")
        try:
            session = build_session(
                cfg["account"]["cookie_string"],
                cfg["advanced"].get("user_agent", ""),
                cfg["advanced"].get("proxy", ""),
                cfg["advanced"].get("timeout_s", 5))
            valid = validate_session(session)
            self._append_log(f"  SESSDATA: {'已获取' if valid['SESSDATA'] else '缺失'}")
            self._append_log(f"  bili_jct: {'已获取' if valid['bili_jct'] else '缺失'}")
            api = MemberBuyAPI(session)
            if api.verify_login():
                self._append_log("[OK] 登录验证通过！")
                self.status_var.set("检查通过")
            else:
                self._append_log("[FAIL] 未登录，请检查 Cookie 是否有效")
        except Exception as e:
            self._append_log(f"[FAIL] 检查出错：{e}")

    def _info(self):
        self._save()
        def run():
            make_gui_logger(self.log_queue)
            try:
                cfg = load_config()
                session, api = self._build_api(cfg)
                flow = OrderFlow(cfg, session, api)
                result = flow.show_project_info()
                if result and result.get("screens"):
                    self.log_queue.put(("autofill", result))
            except Exception as e:
                self.log_queue.put(("log", f"[错误] {e}"))
                self.log_queue.put(("log", traceback.format_exc()))
            finally:
                self.log_queue.put(("done", None))
        self._run_in_thread(run, "查询项目信息")

    def _monitor(self):
        self._save()
        def run():
            make_gui_logger(self.log_queue)
            try:
                cfg = load_config()
                session, api = self._build_api(cfg)
                flow = OrderFlow(cfg, session, api)
                self.log_queue.put(("log", "[*] 预检中..."))
                if not flow.preflight():
                    self.log_queue.put(("log", "[FAIL] 预检未通过"))
                    self.log_queue.put(("done", None))
                    return

                def on_fire():
                    self.log_queue.put(("log", "[*] 倒计时结束！（模拟模式）"))
                    return {"status": "monitor_triggered"}

                if not self.running:
                    self.log_queue.put(("log", "[*] ??????????"))
                    return
                self._scheduler = PrecisionScheduler(
                    cfg["schedule"]["sale_time"],
                    cfg["schedule"].get("advance_ms", 300), on_fire)
                self._scheduler.start()
                self._scheduler = None
            except Exception as e:
                self.log_queue.put(("log", f"[错误] {e}"))
                self.log_queue.put(("log", traceback.format_exc()))
            finally:
                self.log_queue.put(("done", None))
        self._run_in_thread(run, "模拟倒计时")

    def _grab(self):
        self._save()
        def run():
            make_gui_logger(self.log_queue)
            try:
                cfg = load_config()
                session, api = self._build_api(cfg)
                flow = OrderFlow(cfg, session, api)
                self.log_queue.put(("log", "[*] 预检中..."))
                if not flow.preflight():
                    self.log_queue.put(("log", "[FAIL] 预检未通过，已取消"))
                    self.log_queue.put(("done", None))
                    return
                self.log_queue.put(("log",
                    f"目标：{cfg['schedule']['sale_time']}，提前 {cfg['schedule'].get('advance_ms',300)}ms"))
                self.log_queue.put(("log", "等待倒计时..."))

                def on_fire():
                    return flow.execute_grab()

                if not self.running:
                    self.log_queue.put(("log", "[*] ??????????"))
                    return
                self._scheduler = PrecisionScheduler(
                    cfg["schedule"]["sale_time"],
                    cfg["schedule"].get("advance_ms", 300), on_fire)
                result = self._scheduler.start()
                self._scheduler = None
                if result:
                    if result.get("success"):
                        self.log_queue.put(("log", f"!!! 抢票成功！订单号：{result.get('order_id')}"))
                        self.log_queue.put(("log", "前往 https://show.bilibili.com/platform/order.html 支付！"))
                    else:
                        self.log_queue.put(("log", f"[FAIL] {result}"))
            except Exception as e:
                self.log_queue.put(("log", f"[错误] {e}"))
                self.log_queue.put(("log", traceback.format_exc()))
            finally:
                self.log_queue.put(("done", None))
        self._run_in_thread(run, "自动抢票")

    def _stop(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self._append_log("[*] 用户请求停止...")
            if self._scheduler:
                self._scheduler.stop()
                # ?????? scheduler???? worker ????
            self.running = False
            self.btn_grab.configure(state="normal")
            self.status_var.set("已停止")

    def _build_api(self, cfg):
        session = build_session(
            cookie_str=cfg["account"]["cookie_string"],
            user_agent=cfg["advanced"].get("user_agent", ""),
            proxy=cfg["advanced"].get("proxy", ""),
            timeout=cfg["advanced"].get("timeout_s", 5))
        return session, MemberBuyAPI(session)

    def _on_close(self):
        self._stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
