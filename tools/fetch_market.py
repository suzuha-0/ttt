#!/usr/bin/env python3
"""株ノート用の相場データ取得スクリプト。

Yahoo!ファイナンスから
  - 保有銘柄の現在値（国内株・米国株・投資信託）と為替
  - 日本（TOPIX-17業種ETF）と米国（セクターETF）の業種別騰落率・出来高
を取得し、株ノートのデータベースにそのまま書き込める JSON を出力する。

使い方:
  python3 tools/fetch_market.py <portfolio_main.json> <出力ディレクトリ>

portfolio_main.json は ArtifactData で portfolio/main を取得したもの
（{"data": {...}} でも中身だけでもよい）。出力ディレクトリには
  prices-update.json   … portfolio/main への update 用
  heatmap.json         … portfolio/main/market/heatmap への set 用
を書き出す。
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
JST = timezone(timedelta(hours=9))

# 投資信託: 株ノートのコード -> Yahoo!ファイナンスのファンドコード
FUND_CODES = {
    "emaxis-sp500": "03311187",
    "emaxis-allcountry": "0331418A",
    "emaxis-kokusai": "03319172",
    "rakuten-VT": "9I311179",
    "rakuten-VTI": "9I312179",
}

# TOPIX-17業種 ETF（NEXT FUNDS）
JP_SECTORS = [
    ("1617.T", "食品"), ("1618.T", "エネルギー資源"), ("1619.T", "建設・資材"),
    ("1620.T", "素材・化学"), ("1621.T", "医薬品"), ("1622.T", "自動車・輸送機"),
    ("1623.T", "鉄鋼・非鉄"), ("1624.T", "機械"), ("1625.T", "電機・精密"),
    ("1626.T", "情報通信・サービスその他"), ("1627.T", "電力・ガス"),
    ("1628.T", "運輸・物流"), ("1629.T", "商社・卸売"), ("1630.T", "小売"),
    ("1631.T", "銀行"), ("1632.T", "金融（除く銀行）"), ("1633.T", "不動産"),
]
# 東証33業種 -> TOPIX-17業種
TSE33_TO_17 = {
    "水産・農林業": "食品", "食料品": "食品",
    "鉱業": "エネルギー資源", "石油・石炭製品": "エネルギー資源",
    "建設業": "建設・資材", "ガラス・土石製品": "建設・資材", "金属製品": "建設・資材",
    "繊維製品": "素材・化学", "パルプ・紙": "素材・化学", "化学": "素材・化学",
    "医薬品": "医薬品",
    "ゴム製品": "自動車・輸送機", "輸送用機器": "自動車・輸送機",
    "鉄鋼": "鉄鋼・非鉄", "非鉄金属": "鉄鋼・非鉄",
    "機械": "機械",
    "電気機器": "電機・精密", "精密機器": "電機・精密",
    "情報・通信業": "情報通信・サービスその他", "サービス業": "情報通信・サービスその他",
    "その他製品": "情報通信・サービスその他",
    "電気・ガス業": "電力・ガス",
    "陸運業": "運輸・物流", "海運業": "運輸・物流", "空運業": "運輸・物流",
    "倉庫・運輸関連業": "運輸・物流",
    "卸売業": "商社・卸売", "小売業": "小売", "銀行業": "銀行",
    "証券、商品先物取引業": "金融（除く銀行）", "保険業": "金融（除く銀行）",
    "その他金融業": "金融（除く銀行）", "不動産業": "不動産",
}
# 米国セクターETF（Select Sector SPDR）
US_SECTORS = [
    ("XLK", "情報技術"), ("XLC", "コミュニケーション"), ("XLY", "一般消費財"),
    ("XLP", "生活必需品"), ("XLV", "ヘルスケア"), ("XLF", "金融"),
    ("XLI", "資本財"), ("XLE", "エネルギー"), ("XLB", "素材"),
    ("XLU", "公益"), ("XLRE", "不動産"),
]
# 米国の保有銘柄のセクター（Yahooのチャートデータには業種がないため手動）
US_HOLDING_SECTOR = {"SPCX": "資本財"}


def get(url):
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception as e:  # 429などは少し待って再試行
            if attempt == 3:
                raise
            time.sleep(2 ** (attempt + 1))


def chart(symbol, rng="6mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
    d = json.loads(get(url))["chart"]["result"][0]
    q = d["indicators"]["quote"][0]
    rows = [(t, c, v) for t, c, v in zip(d.get("timestamp", []), q.get("close", []), q.get("volume", []))
            if c is not None]
    # 当日分が日足にまだ入っていないことがあるので、最新値で補う
    meta = d["meta"]
    mt, mp = meta.get("regularMarketTime"), meta.get("regularMarketPrice")
    off = meta.get("gmtoffset", 0)
    day = lambda ts: datetime.fromtimestamp(ts + off, timezone.utc).strftime("%Y-%m-%d")  # 取引所の現地日付
    if mt and mp is not None and rows and day(mt) > day(rows[-1][0]):
        rows.append((mt, mp, meta.get("regularMarketVolume") or 0))
    return meta, rows


def stock_page(code):
    """Yahoo!ファイナンス（日本）の銘柄ページから業種を読む。"""
    s = get(f"https://finance.yahoo.co.jp/quote/{code}.T")
    m = re.search(r'industryName--link[^>]*>([^<]+)</a>', s)
    return m.group(1) if m else None


def fund_price(fund_code):
    s = get(f"https://finance.yahoo.co.jp/quote/{fund_code}")
    m = re.search(r'\\"priceBoard\\":\{\\"code\\":\\"' + re.escape(fund_code) +
                  r'\\".*?\\"price\\":\{\\"value\\":\\"([\d,\.]+)\\".*?\\"updateDate\\":\\"([\d/]+)\\"', s, re.S)
    if not m:
        raise ValueError(f"基準価額が見つかりません: {fund_code}")
    return float(m.group(1).replace(",", "")), m.group(2)


def jst_date(ts):
    return datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d")


def perf(rows):
    closes = [c for _, c, _ in rows]
    vols = [v or 0 for _, _, v in rows]
    last = closes[-1]

    def ret(n):
        return round((last / closes[-1 - n] - 1) * 100, 2) if len(closes) > n else None

    recent = vols[-5:]
    base = vols[-25:-5]
    vol_ratio = round((sum(recent) / len(recent)) / (sum(base) / len(base)), 2) if base and sum(base) else None
    return {"last": last, "d1": ret(1), "w1": ret(5), "m1": ret(21), "m3": ret(63), "volRatio": vol_ratio,
            "asOf": jst_date(rows[-1][0])}


def main():
    src, out = sys.argv[1], sys.argv[2]
    main_doc = json.load(open(src, encoding="utf-8"))
    main_doc = main_doc.get("data", main_doc)
    inst = main_doc.get("inst", {})

    prices, price_dates, errors, sectors = {}, {}, [], {}
    for code, info in sorted(inst.items()):
        kind = info.get("kind")
        try:
            if kind == "jp":
                meta, rows = chart(f"{code}.T", "5d")
                prices[code] = meta["regularMarketPrice"]
                price_dates[code] = jst_date(meta["regularMarketTime"])
                ind = stock_page(code)
                # 表記ゆれ（「情報・通信」と「情報・通信業」など）を吸収して対応づける
                norm = {k.rstrip("業"): v for k, v in TSE33_TO_17.items()}
                if ind and ind.rstrip("業") in norm:
                    sectors[code] = {"market": "jp", "sector": norm[ind.rstrip("業")], "tse33": ind}
            elif kind == "us":
                meta, rows = chart(code, "5d")
                prices[code] = meta["regularMarketPrice"]
                price_dates[code] = datetime.fromtimestamp(meta["regularMarketTime"], timezone.utc).strftime("%Y-%m-%d")
                if code in US_HOLDING_SECTOR:
                    sectors[code] = {"market": "us", "sector": US_HOLDING_SECTOR[code]}
            elif kind == "fund" and code in FUND_CODES:
                v, d = fund_price(FUND_CODES[code])
                prices[code] = v
                price_dates[code] = d
        except Exception as e:
            errors.append(f"{code}: {e}")
        time.sleep(0.5)

    fx = {}
    for ccy, sym in (("USD", "JPY=X"), ("EUR", "EURJPY=X")):
        try:
            meta, _ = chart(sym, "5d")
            fx[ccy] = round(meta["regularMarketPrice"], 2)
        except Exception as e:
            errors.append(f"{ccy}: {e}")

    heat = {"jp": [], "us": []}
    for market, items in (("jp", JP_SECTORS), ("us", US_SECTORS)):
        for sym, name in items:
            try:
                _, rows = chart(sym)
                heat[market].append({"symbol": sym.replace(".T", ""), "name": name, **perf(rows)})
            except Exception as e:
                errors.append(f"{sym}: {e}")
            time.sleep(0.3)

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    update = {
        "prices": prices,
        "priceDates": price_dates,
        "fx": fx,
        "fxAsOf": f"{now}（Yahoo!ファイナンス）",
        "pricesUpdatedAt": now,
        "sectors": sectors,
    }
    heatmap = {"updatedAt": now, "source": "Yahoo!ファイナンス", **heat}
    json.dump(update, open(f"{out}/prices-update.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(heatmap, open(f"{out}/heatmap.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"prices": len(prices), "fx": fx, "heat_jp": len(heat["jp"]), "heat_us": len(heat["us"]),
                      "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
