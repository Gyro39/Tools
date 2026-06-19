"""
BW Ticket Grabber -- Main entry point.
Usage:
  python main.py info          Show project info (screens, skus, sale time)
  python main.py monitor        Monitor sale time countdown
  python main.py grab           Full auto-grab mode
  python main.py check          Validate config and session
"""
import sys
import json
import time
from pathlib import Path

from logger import setup_logger
from bilibili.session import build_session, validate_session
from bilibili.member_buy import MemberBuyAPI
from bilibili.order_flow import OrderFlow
from scheduler import PrecisionScheduler

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[!] config.json not found!")
        print(f"    Copy config.example.json to config.json and fill in your info.")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_api(config: dict):
    session = build_session(
        cookie_str=config["account"]["cookie_string"],
        user_agent=config["advanced"].get("user_agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"),
        proxy=config["advanced"].get("proxy", ""),
        timeout=config["advanced"].get("timeout_s", 5),
    )
    api = MemberBuyAPI(session)
    return session, api


def cmd_info(config: dict):
    """Show project info to help fill screen_id and sku_id."""
    _, api = build_api(config)
    flow = OrderFlow(config, None, api)
    flow.show_project_info()


def cmd_monitor(config: dict):
    """Monitor countdown without actually grabbing."""
    sale_time = config["schedule"]["sale_time"]
    advance = int(config["schedule"].get("advance_ms", 300))
    
    _, api = build_api(config)
    flow = OrderFlow(config, None, api)
    
    # Preflight
    print("[*] Running preflight checks...")
    if not flow.preflight():
        print("[!] Preflight failed. Fix issues before grabbing.")
        return
    
    # Also fetch project to show sale time from server
    ts = api.get_sale_timestamp(str(config["project"]["project_id"]))
    if ts:
        from datetime import datetime
        server_time = datetime.fromtimestamp(ts)
        print(f"[*] Server reports sale at: {server_time}")
    
    print(f"[*] Configured sale time: {sale_time}")
    print(f"[*] Will fire {advance}ms before sale")
    print("[*] Monitoring (Ctrl+C to stop)...")
    print()
    
    def on_fire():
        print("[*] Timer fired! (monitor mode - not actually grabbing)")
        return {"status": "monitor_triggered"}
    
    scheduler = PrecisionScheduler(sale_time, advance, on_fire)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\n[*] Monitoring stopped.")


def cmd_grab(config: dict):
    """Full auto-grab mode."""
    sale_time = config["schedule"]["sale_time"]
    advance = int(config["schedule"].get("advance_ms", 300))
    
    logger = setup_logger()
    
    session, api = build_api(config)
    flow = OrderFlow(config, session, api)
    
    # Preflight
    logger.info("Running preflight checks...")
    if not flow.preflight():
        logger.error("Preflight failed. Aborting.")
        return
    
    logger.info(f"Target: {sale_time}, advance={advance}ms")
    logger.info(f"Ticket: project={flow.project_id} screen={flow.screen_id} "
                f"sku={flow.sku_id} count={flow.ticket_count}")
    logger.info("Waiting for countdown...")
    print()
    
    def on_fire():
        return flow.execute_grab()
    
    scheduler = PrecisionScheduler(sale_time, advance, on_fire)
    try:
        result = scheduler.start()
        if result:
            if result.get("success"):
                logger.info(f"SUCCESS! Order ID: {result.get('order_id')}")
                logger.info("Go to https://show.bilibili.com/platform/order.html to pay!")
            else:
                logger.error(f"Failed: {result}")
        else:
            logger.error("No result returned.")
    except KeyboardInterrupt:
        scheduler.stop()
        logger.info("Grab cancelled by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}")


def cmd_check(config: dict):
    """Validate config and session."""
    all_ok = True
    
    # Check project config
    proj = config["project"]
    for key in ["project_id", "screen_id", "sku_id", "ticket_count"]:
        val = proj.get(key)
        if not val and val != 0:
            print(f"  [FAIL] config.project.{key} is missing")
            all_ok = False
    
    # Check buyers
    buyers = config.get("buyers", [])
    if not buyers:
        print("  [FAIL] No buyers configured")
        all_ok = False
    else:
        for i, b in enumerate(buyers):
            for k in ["name", "id_number", "phone"]:
                if not b.get(k):
                    print(f"  [FAIL] buyer[{i}].{k} is missing")
                    all_ok = False
    
    # Check account
    cookie_str = config["account"].get("cookie_string", "")
    if not cookie_str:
        print("  [FAIL] account.cookie_string is empty")
        all_ok = False
    else:
        session, api = build_api(config)
        valid = validate_session(session)
        print(f"  Cookies: SESSDATA={'found' if valid['SESSDATA'] else 'MISSING'}, "
              f"bili_jct={'found' if valid['bili_jct'] else 'MISSING'}")
        if valid["SESSDATA"] and valid["bili_jct"]:
            logged_in = api.verify_login()
            print(f"  Login: {'OK' if logged_in else 'FAILED - check cookie freshness'}")
            if not logged_in:
                all_ok = False
    
    # Check schedule
    sched = config["schedule"]
    if not sched.get("sale_time"):
        print("  [FAIL] schedule.sale_time is missing")
        all_ok = False
    
    if all_ok:
        print("\n  All checks passed!")
    else:
        print("\n  Some checks failed - fix before grabbing.")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    
    cmd = sys.argv[1].lower()
    config = load_config()
    
    commands = {
        "info": cmd_info,
        "monitor": cmd_monitor,
        "grab": cmd_grab,
        "check": cmd_check,
    }
    
    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)
    
    commands[cmd](config)


if __name__ == "__main__":
    main()
