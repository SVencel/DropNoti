#!/usr/bin/env python3
import os, json, re, hashlib
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
STATE_FILE = Path(".r6_drops_state.json")  # stored in repo workspace
TIMEOUT = 20

HEADERS = {"User-Agent": "Mozilla/5.0 (R6DropsWatcher; +https://example.com)"}

def tg_send(text: str, disable_preview=True):
    if not BOT_TOKEN or not CHAT_ID:
        print("Missing TELEGRAM_* env vars; skipping send.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": disable_preview,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        print("Telegram send error:", e)

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": []}

def save_state(state):
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print("State save error:", e)

def seen_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def check_ubisoft_drops():
    url = "https://www.ubisoft.com/twitchdrops"
    items = []
    try:
        html = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(separator=" ", strip=True)
        if re.search(r"(Rainbow\s*Six|R6(\b|:)|Siege)", text, re.I):
            snippet = ""
            m = re.search(r"(.{0,80}(Rainbow\s*Six|R6|Siege).{0,140})", text, re.I)
            if m:
                snippet = m.group(0).strip()
            items.append({
                "source": "Ubisoft Drops",
                "title": "R6 mentioned on Ubisoft Twitch Drops",
                "url": url,
                "details": snippet
            })
    except Exception as e:
        print("Ubisoft check error:", e)
    return items

def check_twitch_campaigns():
    url = "https://www.twitch.tv/drops/campaigns"
    items = []
    try:
        html = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
        text = BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)
        if re.search(r"(Rainbow\s*Six|Tom\s*Clancy.*Rainbow\s*Six\s*Siege|R6)", text, re.I):
            snippet = ""
            m = re.findall(r"(.{0,80}(Rainbow\s*Six|Siege).{0,160})", text, re.I)
            if m:
                snippet = " … ".join([a[0].strip() for a in m[:2]])
            items.append({
                "source": "Twitch Campaigns",
                "title": "R6 mentioned on Twitch Drops Campaigns",
                "url": url,
                "details": snippet
            })
    except Exception as e:
        print("Twitch campaigns check error:", e)
    return items

def check_reddit_r6_drops():
    url = "https://www.reddit.com/r/Rainbow6/search.json?q=drops&restrict_sr=1&sort=new&t=month"
    items = []
    try:
        r = requests.get(url, headers={"User-Agent": "R6DropsWatcher/1.0"}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            title = post.get("title", "")
            if not title:
                continue
            if re.search(r"\bdrops?\b", title, re.I):
                permalink = post.get("permalink", "")
                items.append({
                    "source": "r/Rainbow6",
                    "title": title,
                    "url": "https://www.reddit.com" + permalink,
                    "details": f"Author: u/{post.get('author','unknown')}"
                })
    except Exception as e:
        print("Reddit check error:", e)
    return items

def main():
    state = load_state()
    seen = set(state.get("seen", []))

    new_items = []
    for checker in (check_ubisoft_drops, check_twitch_campaigns, check_reddit_r6_drops):
        for it in checker():
            key = seen_key(f"{it['source']}|{it['title']}|{it.get('url','')}")
            if key not in seen:
                seen.add(key)
                new_items.append(it)

    if new_items:
        for it in new_items:
            msg = (
                f"👀 <b>New R6 Drops Signal</b>\n"
                f"Source: <b>{it['source']}</b>\n"
                f"Title: {it['title']}\n"
                f"{('Details: ' + it['details'][:400]) if it.get('details') else ''}\n"
                f"🔗 {it.get('url','')}"
            ).strip()
            tg_send(msg)
        state["seen"] = list(seen)
        save_state(state)
    else:
        print("No new items.")

if __name__ == "__main__":
    main()
