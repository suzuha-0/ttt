"""Yahoo!ファイナンスの業種別銘柄一覧（東証33業種）から、上場銘柄を全件取得する。

build_universe.py（業種ごとの代表20社）と fetch_market.py（銘柄検索用の一覧）で使う。
"""
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# 東証33業種（Yahoo!ファイナンスの業種コード）
TSE33 = [
    ("0050", "水産・農林業"), ("1050", "鉱業"), ("2050", "建設業"), ("3050", "食料品"),
    ("3100", "繊維製品"), ("3150", "パルプ・紙"), ("3200", "化学"), ("3250", "医薬品"),
    ("3300", "石油・石炭製品"), ("3350", "ゴム製品"), ("3400", "ガラス・土石製品"), ("3450", "鉄鋼"),
    ("3500", "非鉄金属"), ("3550", "金属製品"), ("3600", "機械"), ("3650", "電気機器"),
    ("3700", "輸送用機器"), ("3750", "精密機器"), ("3800", "その他製品"), ("4050", "電気・ガス業"),
    ("5050", "陸運業"), ("5100", "海運業"), ("5150", "空運業"), ("5200", "倉庫・運輸関連業"),
    ("5250", "情報・通信業"), ("6050", "卸売業"), ("6100", "小売業"), ("7050", "銀行業"),
    ("7100", "証券、商品先物取引業"), ("7150", "保険業"), ("7200", "その他金融業"), ("8050", "不動産業"),
    ("9050", "サービス業"),
]

TOTAL = re.compile(r'<!-- -->(\d[\d,]*)<!-- -->件中')
ITEM_START = '{"detailLink":"https://finance.yahoo.co.jp/quote/'


def get(url):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception:  # 429などは少し待って再試行
            if attempt == 4:
                raise
            time.sleep(2 ** (attempt + 1))


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _items(html):
    """一覧ページに埋め込まれた銘柄データ（JSON）を取り出す。"""
    dec = json.JSONDecoder()
    pos = 0
    while True:
        i = html.find(ITEM_START, pos)
        if i < 0:
            return
        try:
            obj, end = dec.raw_decode(html, i)
        except ValueError:
            pos = i + 1
            continue
        pos = end
        yield obj


def _sector(item):
    code, name = item
    first = get(f"https://finance.yahoo.co.jp/search/qi/?ids={code}")
    m = TOTAL.search(first)
    total = int(m.group(1).replace(",", "")) if m else 20
    pages = [first] + [get(f"https://finance.yahoo.co.jp/search/qi/?ids={code}&page={p}")
                       for p in range(2, (total + 19) // 20 + 1)]
    seen = {}
    for html in pages:
        for o in _items(html):
            c = o.get("code")
            if not c or c in seen:
                continue
            comment = (o.get("comment") or "").replace("【特色】", "").strip()
            seen[c] = {
                "code": c,
                "name": o.get("name", ""),
                "market": o.get("marketName", ""),
                "sector": name,
                "price": _num(o.get("price")),
                "chg": _num((o.get("priceChangeRate") or {}).get("value")),
                "cap": _num((o.get("totalPrice") or {}).get("value")),  # 百万円
                "date": o.get("latestPriceTime", ""),
                "desc": comment,
            }
    return name, total, list(seen.values())


def scrape_listing(workers=4, log=None):
    """東証33業種すべての銘柄を返す: {業種名: [銘柄, ...]}"""
    out = {}
    with ThreadPoolExecutor(workers) as ex:
        for name, total, items in ex.map(_sector, TSE33):
            out[name] = items
            if log:
                log(f"{name}: {total}社中 {len(items)}社")
    return out
