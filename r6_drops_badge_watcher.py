#!/usr/bin/env python3
import os, time, re, json, pathlib, hashlib
from typing import List
import requests
from playwright.sync_api import sync_playwright, TimeoutError as TE

CATEGORY_URL = "https://www.twitch.tv/directory/category/tom-clancys-rainbow-six-siege?sort=VIEWER_COUNT"

# Optional Telegram env vars
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = pathlib.Path(".r6_drops_badge_seen.json")

# Some UIs show “Drops”, others “Drops Enabled” / localized strings.
DROPS_TEXT = re.compile(r"\bdrops\b", re.I)

def tg_send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)  # just print if Telegram not configured
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=20)
    try:
        r.raise_for_status()
    except Exception as e:
        print("Telegram error:", e, getattr(r, "text", ""))

def gentle_scroll(page, loops=12, dy=1800, delay=0.2):
    for _ in range(loops):
        page.mouse.wheel(0, dy)
        time.sleep(delay)

def load_seen() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("seen", []))
        except Exception:
            pass
    return set()

def save_seen(seen: set):
    STATE_FILE.write_text(json.dumps({"seen": list(seen)}, ensure_ascii=False, indent=2), encoding="utf-8")

def main():
    seen = load_seen()
    messages: List[str] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/118 Safari/537.36")
        )
        page = context.new_page()
        page.goto(CATEGORY_URL, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except TE:
            pass

        # Load many cards
        gentle_scroll(page, loops=15)

        # Each stream card is an <a> to /<channel>, with various badges inside.
        cards = page.locator("a[href^='https://www.twitch.tv/'], a[href^='/']").filter(has_text=re.compile(r".+"))
        count = cards.count()

        drops_found = []
        for i in range(count):
            card = cards.nth(i)
            try:
                # Read a small text snapshot of the card
                txt = card.inner_text(timeout=1000)
            except Exception:
                continue
            if not txt:
                continue

            if DROPS_TEXT.search(txt):
                href = card.get_attribute("href") or ""
                if href.startswith("/"):
                    href = "https://www.twitch.tv" + href
                # Build a compact message and dedupe key
                key = hashlib.sha256(href.encode("utf-8")).hexdigest()
                if key in seen:
                    continue
                drops_found.append((href, txt))
                seen.add(key)

        browser.close()

    if drops_found:
        for href, txt in drops_found[:10]:  # cap messages
            # Extract channel name & a short snippet
            channel = href.split("/")[-1]
            snippet = " ".join(txt.split())[:160]
            parts = [
                "🟣 R6 streams show **Drops Enabled**",
                f"Channel: {channel}",
                f"Link: {href}",
                f"Snippet: {snippet}"
            ]
            tg_send("\n".join(parts))
        save_seen(seen)
    else:
        print("No R6 streams with a Drops badge right now.")

if __name__ == "__main__":
    main()
