#!/usr/bin/env python3
import os, time, re, json, pathlib, hashlib
from typing import List, Tuple
import requests
from playwright.sync_api import sync_playwright, TimeoutError as TE

CATEGORY_URL = "https://www.twitch.tv/directory/category/tom-clancys-rainbow-six-siege?sort=VIEWER_COUNT"

# Telegram (optional)
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# State (to avoid duplicate alerts across runs if you want)
STATE_FILE = pathlib.Path(".r6_drops_badge_seen.json")

# Badge selectors: we prefer explicit “Drops” badge elements over generic text
BADGE_LOCATORS = [
    "[data-test-selector*='Drops']",                      # common internal selector
    "[aria-label*='Drops' i]",                            # badge with aria-label
    "img[alt*='Drops' i]",                                # image alt
    "span:has-text('Drops')", "div:has-text('Drops')"     # text fallback
]

def tg_send(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}, timeout=20)
    try:
        r.raise_for_status()
    except Exception as e:
        print("Telegram error:", e, getattr(r, "text", ""))

def gentle_scroll(page, loops=14, dy=2000, delay=0.2):
    for _ in range(loops):
        page.mouse.wheel(0, dy)
        time.sleep(delay)

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_digest": ""}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def find_stream_cards(page):
    # Twitch stream cards are anchors linking to /<channel> (sometimes absolute)
    return page.locator("a[href^='/'], a[href^='https://www.twitch.tv/']")

def card_has_drops(card) -> bool:
    # Look for the badge *inside* the card using robust selectors
    for sel in BADGE_LOCATORS:
        try:
            loc = card.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    # Last fallback: tiny text snapshot (avoid if possible)
    try:
        txt = card.inner_text(timeout=500)
        if txt and re.search(r"\bdrops\b", txt, re.I):
            return True
    except Exception:
        pass
    return False

def normalize_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/"):
        href = "https://www.twitch.tv" + href
    return href.split("?")[0].rstrip("/")

def channel_from_href(href: str) -> str:
    h = href.rstrip("/").split("/")
    return h[-1] if h else href

def main():
    state = load_state()

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

        # Load a good number of cards
        gentle_scroll(page, loops=15)

        cards = find_stream_cards(page)
        count = cards.count()

        drops_streams: List[Tuple[str, str]] = []  # (channel_link, channel_name)
        for i in range(count):
            card = cards.nth(i)
            try:
                href = normalize_href(card.get_attribute("href") or "")
            except Exception:
                continue
            if not href or href.count("/") < 3:  # skip non-channel links like /directory/...
                continue
            # Only consider links that look like /<channel>
            if not re.match(r"^https://www\.twitch\.tv/[^/]+$", href, re.I):
                continue

            if card_has_drops(card):
                chan = channel_from_href(href)
                drops_streams.append((href, chan))

        browser.close()

    # De-duplicate by channel
    unique = {}
    for href, chan in drops_streams:
        unique[chan.lower()] = href
    channels = sorted(unique.keys())  # stable order
    total = len(channels)

    if total == 0:
        print("No R6 streams with a Drops badge right now.")
        return

    # Create a digest (so we can avoid re-sending unchanged info if you want)
    digest = "|".join(channels)
    if digest == state.get("last_digest", ""):
        print("Drops still active, but set of channels unchanged — not notifying.")
        return

    # Build one compact message
    sample = channels[:5]
    remaining = total - len(sample)
    parts = [
        f"🟣 R6 Drops live on {total} stream(s)",
        "Examples: " + ", ".join(sample) + (f" +{remaining} more" if remaining > 0 else ""),
        "Category: https://www.twitch.tv/directory/category/tom-clancys-rainbow-six-siege?sort=VIEWER_COUNT"
    ]
    msg = "\n".join(parts)
    tg_send(msg)

    # Save digest so the next run won’t re-send the same set
    state["last_digest"] = digest
    save_state(state)

if __name__ == "__main__":
    main()
