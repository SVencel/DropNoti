#!/usr/bin/env python3
import os, re, json, time, base64, pathlib, hashlib
from typing import List, Dict, Any, Optional
import requests
from playwright.sync_api import sync_playwright, TimeoutError as TE

ROOT = pathlib.Path(".").resolve()
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SNAPSHOT = DATA_DIR / "campaigns.json"
TMP_STATE = ROOT / "twitch_storage_state.json"  # reconstructed at runtime

# --- Telegram (optional) ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# Comma-separated slugs to notify about (e.g. "tom-clancys-rainbow-six-siege,hades-ii")
TARGET_SLUGS = {
    s.strip().lower()
    for s in os.getenv("TARGET_SLUGS", "tom-clancys-rainbow-six-siege").split(",")
    if s.strip()
}

URL = "https://www.twitch.tv/drops/campaigns"

# --- Regex helpers ---
TIME_RE  = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|GMT|UTC|CET|CEST)", re.I)
WATCH_RE = re.compile(r"\bwatch\b.*?\b(\d+(\.\d+)?)\s*(hours?|hrs?|h)\b", re.I)
DROP_RE  = re.compile(r"\bdrop(s)?\b", re.I)

def restore_state_from_secret() -> None:
    """Recreate twitch_storage_state.json from TWITCH_STATE_B64 if not present."""
    if TMP_STATE.exists():
        return
    b64 = os.getenv("TWITCH_STATE_B64", "")
    if not b64:
        raise SystemExit("Missing TWITCH_STATE_B64 secret (base64 of your twitch_storage_state.json).")
    raw = base64.b64decode(b64.encode())
    TMP_STATE.write_bytes(raw)

def tg_send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print("Telegram send error:", e)

def gentle_scroll(page, loops=14, dy=2000, delay=0.18):
    for _ in range(loops):
        page.mouse.wheel(0, dy)
        time.sleep(delay)

def scroll_to_end(page, max_loops=25, delay=0.25):
    last = 0
    for _ in range(max_loops):
        h = page.evaluate("() => document.scrollingElement.scrollHeight")
        if h <= last:
            break
        last = h
        page.mouse.wheel(0, 3200)
        time.sleep(delay)

def get_root(page):
    root = page.locator("div[class*='drops-root__content']").first
    return root if root.count() else page

def row_text(el) -> str:
    try:
        txt = el.inner_text(timeout=2000)
    except Exception:
        try:
            txt = el.evaluate("(el)=>el.textContent") or ""
        except Exception:
            txt = ""
    return " ".join((txt or "").split())

def row_links(el) -> List[str]:
    links = []
    try:
        anchors = el.locator("a[href]")
        for i in range(anchors.count()):
            href = anchors.nth(i).get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.twitch.tv" + href
            links.append(href)
    except Exception:
        pass
    return links

def extract_game_from_links(links: List[str]) -> Optional[Dict[str, str]]:
    """Find /directory/category/<slug> and return {slug}."""
    for href in links:
        m = re.search(r"https://www\.twitch\.tv/directory/(category/)?([^/?#]+)", href, re.I)
        if m:
            slug = m.group(2).lower()
            return {"game_slug": slug}
    return None

def parse_campaign_row(el) -> Optional[Dict[str, Any]]:
    text = row_text(el)
    if not text or len(text) < 20:
        return None

    links = row_links(el)
    game_info = extract_game_from_links(links)
    if not game_info:
        return None

    lines = [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip()]

    # game name guess
    game_name = ""
    for ln in lines[:4]:
        if len(ln) <= 50 and "campaign" not in ln.lower():
            game_name = ln
            break

    # timeframe
    timeframe = ""
    for ln in lines:
        if TIME_RE.search(ln):
            timeframe = ln
            break

    # rewards/watch lines
    rewards: List[str] = []
    for ln in lines:
        if WATCH_RE.search(ln) or (DROP_RE.search(ln) and len(ln) <= 160):
            rewards.append(ln)

    # title
    title = ""
    for ln in lines[:6]:
        low = ln.lower()
        if ("drop" in low or "campaign" in low or "watch" in low) and len(ln) <= 120:
            title = ln
            break
    if not title and lines:
        title = lines[0]

    start_raw = ""
    end_raw = ""
    if " - " in timeframe:
        start_raw, end_raw = [s.strip() for s in timeframe.split(" - ", 1)]

    item = {
        "game_name": game_name or game_info["game_slug"].replace("-", " ").title(),
        "game_slug": game_info["game_slug"],
        "campaign_title": title,
        "timeframe": timeframe,
        "start_raw": start_raw or None,
        "end_raw": end_raw or None,
        "rewards": rewards,
        "raw_text": text,
    }

    basis = f"{item['game_slug']}|{item['timeframe']}|{','.join(item['rewards'])}"
    item["id"] = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return item

def scrape_all_campaigns() -> Dict[str, Any]:
    if not TMP_STATE.exists():
        restore_state_from_secret()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=str(TMP_STATE),
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118 Safari/537.36")
        )
        page = context.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=35000)
        except TE:
            pass
        gentle_scroll(page, loops=8)
        scroll_to_end(page, max_loops=30)

        root = get_root(page)
        rows = root.locator(":scope > div").filter(has_text=re.compile(r".+"))
        total = rows.count()

        items: List[Dict[str, Any]] = []
        for i in range(total):
            row = rows.nth(i)
            try:
                btn = row.locator("button[aria-expanded]").first
                if btn and btn.count() and btn.is_visible():
                    btn.click(timeout=1200)
                    time.sleep(0.15)
            except Exception:
                pass
            it = parse_campaign_row(row)
            if it:
                items.append(it)

        browser.close()

    dedup: Dict[str, Dict[str, Any]] = {}
    for it in items:
        dedup[it["id"]] = it

    snapshot = {
        "scraped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "count": len(dedup),
        "campaigns": list(dedup.values()),
    }
    return snapshot

def load_previous() -> Dict[str, Any]:
    if SNAPSHOT.exists():
        try:
            return json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"campaigns": []}

def diff_campaigns(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    old_map = {c["id"]: c for c in old.get("campaigns", [])}
    new_map = {c["id"]: c for c in new.get("campaigns", [])}
    added = [new_map[k] for k in new_map.keys() - old_map.keys()]
    removed = [old_map[k] for k in old_map.keys() - new_map.keys()]
    changed = []
    for k in old_map.keys() & new_map.keys():
        if (old_map[k].get("timeframe") != new_map[k].get("timeframe")) or (old_map[k].get("rewards") != new_map[k].get("rewards")):
            changed.append(new_map[k])
    return {"added": added, "removed": removed, "changed": changed}

def notify_for_targets(d: Dict[str, List[Dict[str, Any]]]):
    added   = [c for c in d["added"]   if c["game_slug"] in TARGET_SLUGS]
    changed = [c for c in d["changed"] if c["game_slug"] in TARGET_SLUGS]

    if not added and not changed:
        print("No new/updated campaigns for target games.")
        return

    lines = ["🎁 Twitch Drops update (targets)"]
    if added:
        lines.append(f"New: {len(added)}")
        for c in added[:3]:
            lines.append(f"• {c['game_name']}: {c.get('timeframe') or 'Dates TBA'}")
    if changed:
        lines.append(f"Updated: {len(changed)}")
        for c in changed[:2]:
            lines.append(f"• {c['game_name']}: {c.get('timeframe') or 'Dates TBA'}")

    pick = (added + changed)[:1]
    if pick and pick[0].get("rewards"):
        lines.append("Rewards: " + "; ".join(pick[0]["rewards"])[:200])

    tg_send("\n".join(lines))

def main():
    restore_state_from_secret()
    prev = load_previous()
    snap = scrape_all_campaigns()
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    diff = diff_campaigns(prev, snap)
    notify_for_targets(diff)

if __name__ == "__main__":
    main()

