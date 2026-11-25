"""World news band for jacetrading.com: metals, trade and industry headlines from public RSS feeds.

Only the headline, the outlet name and the link are stored (no article text, no images). The site shows them as
link-outs to the original publisher. Korean titles come from the free MyMemory API and are cached by link.

Usage
  python scripts/jace_news.py              # collect, translate and write news.json next to this repo's root
  python scripts/jace_news.py --dry-run    # collect and print, write nothing
  python scripts/jace_news.py --out PATH   # write somewhere else (used for the local site preview)
GitHub Actions runs it every hour (.github/workflows/news.yml) and commits news.json when it changes.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "ko_cache.json")
KST = timezone(timedelta(hours=9))
GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="

# (label, url, mode) mode "metals": every item must name a metal or the metals trade
#                    "biz":    business feed, item must match the JACE topics
#                    "gen":    general news feed, item must match the JACE topics
FEEDS = [
    ("", GN + urllib.parse.quote("(aluminium OR aluminum OR copper OR LME) (price OR prices OR market OR tariff OR smelter OR demand OR supply) when:2d"), "metals"),
    ("", GN + urllib.parse.quote('("scrap metal" OR "aluminium scrap" OR "aluminum scrap" OR "copper scrap" OR "metal recycling" OR "steel tariffs") when:3d'), "metals"),
    ("BBC", "https://feeds.bbci.co.uk/news/business/rss.xml", "biz"),
    ("CNBC", "https://www.cnbc.com/id/10000664/device/rss/rss.html", "biz"),
    ("Guardian", "https://www.theguardian.com/business/rss", "biz"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories", "biz"),
    ("DW", "https://rss.dw.com/rdf/rss-en-bus", "biz"),
    ("Straits Times", "https://www.straitstimes.com/news/business/rss.xml", "biz"),
    ("Yonhap", "https://en.yna.co.kr/RSS/news.xml", "gen"),
    ("Korea Herald", "https://www.koreaherald.com/rss/newsAll", "gen"),
    ("SCMP", "https://www.scmp.com/rss/92/feed", "gen"),
    ("Economic Times", "https://economictimes.indiatimes.com/rssfeedstopstories.cms", "gen"),
]
# publishers whose terms forbid automated copying or republishing: never shown, even via a search feed
BLOCKED_SOURCES = re.compile(r"recycling today|recyclingtoday", re.I)

METAL = re.compile(r"\b(alumin(i)?um|copper|steel|metals?|scrap|recycl\w*|smelt\w*|mining|miners?|lithium|nickel|zinc|"
                   r"iron ore|bauxite|alumina|commodit\w*|lme|comex|brass|cathode)\b", re.I)
TOPIC = re.compile(r"\b(tariffs?|trade|exports?|imports?|shipping|freight|ports?|container\w*|supply chains?|customs|"
                   r"sanctions?|manufactur\w*|factor(y|ies)|industr\w*|carmakers?|automakers?|evs?|batter(y|ies)|"
                   r"construction|china|chinese|korea\w*|yuan|dollar|fed|inflation|recession|gdp|econom\w*|oil|opec|"
                   r"gold|energy)\b", re.I)
BAD = re.compile(r"\b(horoscope|recipe|celebrity|royal|kardashian|nfl|nba|mlb|soccer|football|premier league|oscars?|"
                 r"grammy|sponsored|coupon|black friday|cyber monday|parenting|should (i|we)|my (wife|husband)|"
                 r"retirement|401\(k\)|annuity|social security|medicare|tennis|marries|dies aged|columnist|"
                 r"killed|murder|shooting|stabbing|crypto\w*|bitcoin|quiz|podcast|theft|thieves|stolen|steal\w*|"
                 r"vandal\w*|poison\w*|police|council|planning|obituary|fire|blaze|firefighters|explosion|injured|dead|death|court|jailed)\b|^(watch|live|listen)\b", re.I)
# a metals headline must also be about the market, not a local story that only mentions the metal
MARKET = re.compile(r"\b(prices?|markets?|lme|comex|tariffs?|demand|supply|exports?|imports?|output|production|smelt\w*|"
                    r"stocks|stockpiles?|inventor(y|ies)|rall\w*|slides?|falls?|rises?|surg\w*|slump\w*|deficit|surplus|"
                    r"premiums?|trade\w*|traders?|futures|year-(high|low)|mine|miners?|recycl\w*|plant|capacity)\b", re.I)
MAX_ITEMS = 24
MAX_AGE_H = 48
CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (news headline band)"})
    return urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "replace")


def tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", block, re.S)
    return html.unescape(m.group(1)).strip() if m else ""


def clean(s: str, n: int) -> str:
    return CTRL.sub("", re.sub(r"\s+", " ", s.replace("]]>", ""))).strip()[:n]


def wanted(title: str, mode: str) -> bool:
    if BAD.search(title):
        return False
    if mode == "metals":
        return bool(METAL.search(title) and MARKET.search(title))
    return bool(METAL.search(title) or TOPIC.search(title))


def collect() -> tuple[list[dict], list[str]]:
    now = datetime.now(timezone.utc)
    per, errors = [], []
    for label, url, mode in FEEDS:
        rows = []
        try:
            xml = get(url)
        except Exception as e:  # one dead feed must not stop the rest
            errors.append(f"{label or url[:60]}: {e}")
            continue
        for b in re.findall(r"<item[\s>].*?</item>", xml, re.S)[:40]:
            title, link = clean(tag(b, "title"), 200), tag(b, "link")
            source = label
            if not label:  # Google News search: the outlet is in <source>, and repeated at the end of the title
                source = clean(tag(b, "source"), 40)
                if source and title.endswith(" - " + source):
                    title = title[: -len(source) - 3].strip()
                source = re.split(r"\s+[-|]\s+", source)[0].strip()  # "Outlet - Country News" -> "Outlet"
            title = re.sub(r"^\((Yonhap|LEAD|URGENT)[^)]*\)\s*", "", title)
            if re.search(r"[^ -\u024f\u2013\u2014\u2018-\u201f\u00a3\u20ac]", source + title):
                continue  # English-language outlets only
            if not title or not source or BLOCKED_SOURCES.search(source) or BLOCKED_SOURCES.search(link):
                continue
            if not (link.startswith("https://") or link.startswith("http://")):
                continue
            title = title[:160]
            if len(title) < 25 or not wanted(title, mode):
                continue
            link = link.replace("http://", "https://", 1).split("?at_medium")[0]
            try:
                ts = parsedate_to_datetime(tag(b, "pubDate")).astimezone(timezone.utc)
            except Exception:
                ts = None
            if ts is None and mode == "metals":
                continue
            if ts and (now - ts).total_seconds() > MAX_AGE_H * 3600 * (1.5 if mode == "metals" else 1):
                continue
            rows.append({"s": source, "t": title, "u": link, "ts": ts.timestamp() if ts else 0, "m": mode == "metals"})
        per.append(sorted(rows, key=lambda r: r["ts"], reverse=True))
    # take from each feed in turn so no single outlet fills the band; metals feeds come first in FEEDS
    out, seen_u, seen_t = [], set(), set()
    i = 0
    while len(out) < MAX_ITEMS and any(i < len(p) for p in per):
        for p in per:
            # the metals search feeds give two items per round, the others one
            take = p[2 * i: 2 * i + 2] if p and p[0]["m"] else p[i: i + 1]
            for r in take:
                if len(out) >= MAX_ITEMS:
                    break
                k = re.sub(r"\W+", "", r["t"].lower())[:40]
                if r["u"] in seen_u or k in seen_t:
                    continue
                seen_u.add(r["u"]); seen_t.add(k); out.append(r)
        i += 1
    return out, errors


def translate(text: str) -> str:
    url = "https://api.mymemory.translated.net/get?langpair=en%7Cko&q=" + urllib.parse.quote(text[:200])
    try:
        d = json.loads(get(url))
        if d.get("responseStatus") != 200 or d.get("quotaFinished"):
            return ""
        t = clean(html.unescape(d["responseData"]["translatedText"]), 140)
        # drop results with almost no Hangul (untranslated text or an error notice)
        return t if len(re.findall(r"[가-힣]", t)) >= 4 and "MYMEMORY" not in t.upper() else ""
    except Exception:
        return ""


def korean(items: list[dict]) -> dict:
    try:
        with open(CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    fails = 0
    for r in items:
        if r["u"] in cache or fails >= 3:  # after three failures (quota) the rest stay in English
            continue
        k = translate(r["t"])
        if k:
            cache[r["u"]] = k
        else:
            fails += 1
    live = {r["u"] for r in items}
    old = [(u, k) for u, k in cache.items() if u not in live]
    merged = dict(old[-300:])
    merged.update({u: cache[u] for u in cache if u in live})
    with open(CACHE, "w", encoding="utf-8", newline="\n") as f:
        json.dump(merged, f, ensure_ascii=False)
    return merged


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry-run" in argv
    out_path = os.path.join(ROOT, "news.json")
    if "--out" in argv:
        out_path = argv[argv.index("--out") + 1]
    items, errors = collect()
    for e in errors:
        print("feed error:", e)
    if len(items) < 6:
        print(f"FAIL only {len(items)} headlines, news.json left as it is")
        return 1
    ko = {} if dry else korean(items)
    data = {
        "updated": datetime.now(KST).isoformat(timespec="minutes"),
        "items": [dict({"s": r["s"], "t": r["t"], "u": r["u"]}, **({"k": ko[r["u"]]} if r["u"] in ko else {}))
                  for r in items],
    }
    if dry:
        for r in data["items"]:
            print(f'{r["s"]:<22} {r["t"]}')
        return 0
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    print(f"OK {len(items)} headlines ({sum('k' in r for r in data['items'])} in Korean) -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
