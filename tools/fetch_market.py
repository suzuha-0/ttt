#!/usr/bin/env python3
"""株ノート用の相場データ取得スクリプト。

Yahoo!ファイナンスから
  - 保有銘柄の現在値（国内株・米国株・投資信託）と為替
  - 業種ヒートマップ（日本: 東証33業種、米国: 25業種）と、業種ごとの代表20社の値動き
を取得し、株ノートのデータベースにそのまま書き込める JSON を出力する。
業種ごとの代表20社は tools/universe.json（tools/build_universe.py で作成）を使う。

使い方:
  python3 tools/fetch_market.py <portfolio_main.json> <出力ディレクトリ>

portfolio_main.json は ArtifactData で portfolio/main を取得したもの
（{"data": {...}} でも中身だけでもよい）。出力ディレクトリには
  prices-update.json   … portfolio/main への update 用
  heatmap.json         … portfolio/main/market/heatmap への set 用
  members-jp.json      … portfolio/main/market/members-jp への set 用
  members-us.json      … portfolio/main/market/members-us への set 用
  listing-*.json       … 銘柄検索用の全銘柄一覧（portfolio/main/listing/*）
  db-writes.json       … 上のファイルをどの文書に書くかの一覧（1MB 以下のまとまりごと）
を書き出す。
"""
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jp_listing import scrape_listing  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
JST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))

# 投資信託: 株ノートのコード -> Yahoo!ファイナンスのファンドコード
FUND_CODES = {
    "emaxis-sp500": "03311187",
    "emaxis-allcountry": "0331418A",
    "emaxis-kokusai": "03319172",
    "rakuten-VT": "9I311179",
    "rakuten-VTI": "9I312179",
}
# 米国の保有銘柄の業種（universe.json の米国業種名）
US_HOLDING_SECTOR = {"SPCX": "航空宇宙・防衛"}


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


def jst_date(ts):
    return datetime.fromtimestamp(ts, JST).strftime("%Y-%m-%d")


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


def perf(rows):
    """終値の騰落率（1日・1週・1か月・3か月）と売買代金（直近5日・その前20日の1日平均）。"""
    closes = [c for _, c, _ in rows]
    value = [c * (v or 0) for _, c, v in rows]
    last = closes[-1]

    def ret(n):
        return round((last / closes[-1 - n] - 1) * 100, 2) if len(closes) > n and closes[-1 - n] else None

    v5 = sum(value[-5:]) / len(value[-5:])
    base = value[-25:-5]
    v20 = sum(base) / len(base) if base else 0
    return {"last": last, "d1": ret(1), "w1": ret(5), "m1": ret(21), "m3": ret(63),
            "vr": round(v5 / v20, 2) if v20 else None, "v5": v5, "v20": v20, "asOf": jst_date(rows[-1][0])}


def safe_perf(sym):
    try:
        return sym, perf(chart(sym)[1])
    except Exception as e:
        return sym, {"error": str(e)}


def short_name(name):
    return re.sub(r"[（(]株[）)]|株式会社|\s+$", "", name).replace("　", " ").strip()


def weighted(members, key, weight):
    pts = [(m[key], weight(m)) for m in members if m.get(key) is not None and weight(m)]
    tw = sum(w for _, w in pts)
    return round(sum(v * w for v, w in pts) / tw, 2) if tw else None


def main():
    src, out = sys.argv[1], sys.argv[2]
    main_doc = json.load(open(src, encoding="utf-8"))
    main_doc = main_doc.get("data", main_doc)
    inst = main_doc.get("inst", {})
    universe = json.load(open(os.path.join(HERE, "universe.json"), encoding="utf-8"))
    jp_sector_names = {s["sector"].rstrip("業"): s["sector"] for s in universe["jp"]}

    # ---- 保有銘柄の現在値 ----
    prices, price_dates, errors, sectors = {}, {}, [], {}
    for code, info in sorted(inst.items()):
        kind = info.get("kind")
        try:
            if kind == "jp":
                meta, _ = chart(f"{code}.T", "5d")
                prices[code] = meta["regularMarketPrice"]
                price_dates[code] = jst_date(meta["regularMarketTime"])
                ind = stock_page(code)
                # 表記ゆれ（「情報・通信」と「情報・通信業」など）を吸収して対応づける
                if ind and ind.rstrip("業") in jp_sector_names:
                    sectors[code] = {"market": "jp", "sector": jp_sector_names[ind.rstrip("業")]}
            elif kind == "us":
                meta, _ = chart(code, "5d")
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
        time.sleep(0.3)

    fx = {}
    for ccy, sym in (("USD", "JPY=X"), ("EUR", "EURJPY=X")):
        try:
            meta, _ = chart(sym, "5d")
            fx[ccy] = round(meta["regularMarketPrice"], 2)
        except Exception as e:
            errors.append(f"{ccy}: {e}")

    # ---- 業種ヒートマップ ----
    syms = {f"{m['code']}.T" for s in universe["jp"] for m in s["members"]}
    syms |= {m["code"] for s in universe["us"] for m in s["members"]}
    syms |= {s["etf"] for s in universe["us"]}
    with ThreadPoolExecutor(6) as ex:
        data = dict(ex.map(safe_perf, sorted(syms)))
    failed = sorted(s for s, p in data.items() if "error" in p)
    if failed:
        errors.append(f"取得できなかった銘柄 {len(failed)}件: {' '.join(failed[:20])}")

    heat = {"jp": [], "us": []}
    members = {"jp": {}, "us": {}}
    for market in ("jp", "us"):
        for s in universe[market]:
            rows = []
            for m in s["members"]:
                p = data.get(f"{m['code']}.T" if market == "jp" else m["code"])
                if not p or "error" in p:
                    continue
                rows.append({"code": m["code"], "name": short_name(m["name"]), "cap": m.get("cap"),
                             **{k: p[k] for k in ("last", "d1", "w1", "m1", "m3", "vr", "v5", "v20")}})
            if not rows:
                continue
            v5, v20 = sum(r["v5"] for r in rows), sum(r["v20"] for r in rows)
            row = {"name": s["sector"], "count": len(rows), "vr": round(v5 / v20, 2) if v20 else None}
            if market == "jp":
                # 日本: 代表20社の時価総額加重平均
                for k in ("d1", "w1", "m1", "m3"):
                    row[k] = weighted(rows, k, lambda r: r.get("cap"))
                row["asOf"] = data[f"{s['members'][0]['code']}.T"].get("asOf")
                row["basis"] = "代表20社の時価総額加重"
            else:
                # 米国: 業種ETFの値動き
                e = data.get(s["etf"], {})
                for k in ("d1", "w1", "m1", "m3"):
                    row[k] = e.get(k)
                row["asOf"] = e.get("asOf")
                row["etf"] = s["etf"]
                row["basis"] = f"ETF {s['etf']}"
            heat[market].append(row)
            members[market][s["sector"]] = [
                {k: (round(r[k], 2) if isinstance(r[k], float) else r[k]) for k in ("code", "name", "last", "d1", "w1", "m1", "m3", "vr")}
                for r in rows]

    # ---- ウォッチリスト ----
    watch_prices = {}
    for w in main_doc.get("watch", []) or []:
        code = w.get("code")
        sym = f"{code}.T" if w.get("market") == "jp" else code
        p = data.get(sym)
        if not p or "error" in p:
            _, p = safe_perf(sym)
        if p and "error" not in p:
            watch_prices[code] = {k: (round(p[k], 2) if isinstance(p[k], float) else p[k])
                                  for k in ("last", "d1", "w1", "m1", "m3", "vr", "asOf")}
        else:
            errors.append(f"ウォッチ {code}: 取得できず")

    # ---- 銘柄検索用の一覧（日本株は全上場銘柄、米国株は代表銘柄） ----
    listing = scrape_listing()
    sector_names = [name for name in listing]
    items = []
    for si, name in enumerate(sector_names):
        for x in listing[name]:
            items.append([x["code"], short_name(x["name"]), x["market"].replace("東証", ""), si,
                          x["price"], x["chg"], x["cap"], x["date"], x["desc"]])
    items.sort(key=lambda r: r[0])
    chunks, cur, size = [], [], 0
    for r in items:
        n = len(json.dumps(r, ensure_ascii=False).encode("utf-8"))
        if cur and size + n > 180_000:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(r)
        size += n
    if cur:
        chunks.append(cur)

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")
    update = {
        "prices": prices,
        "priceDates": price_dates,
        "fx": fx,
        "fxAsOf": f"{now}（Yahoo!ファイナンス）",
        "pricesUpdatedAt": now,
        "sectors": sectors,
        "watchPrices": watch_prices,
    }
    heatmap = {"updatedAt": now, "source": "Yahoo!ファイナンス", **heat}
    dump = lambda name, obj: json.dump(obj, open(f"{out}/{name}", "w", encoding="utf-8"), ensure_ascii=False)
    dump("prices-update.json", update)
    dump("heatmap.json", heatmap)
    dump("members-jp.json", {"updatedAt": now, "sectors": members["jp"]})
    dump("members-us.json", {"updatedAt": now, "sectors": members["us"]})
    for i, c in enumerate(chunks):
        dump(f"listing-jp-{i}.json", {"rows": c})
    dump("listing-info.json", {"updatedAt": now, "chunks": len(chunks), "count": len(items),
                               "fields": ["code", "name", "market", "sector", "price", "chg", "cap", "date", "desc"],
                               "sectors": sector_names})

    # どのファイルをどの文書に書くか（ArtifactData の batch は1回1MBまでなので分ける）
    files = [("portfolio", "main", "update", "prices-update.json"),
             ("portfolio/main/market", "heatmap", "set", "heatmap.json"),
             ("portfolio/main/market", "members-jp", "set", "members-jp.json"),
             ("portfolio/main/market", "members-us", "set", "members-us.json")]
    files += [("portfolio/main/listing", f"jp-{i}", "set", f"listing-jp-{i}.json") for i in range(len(chunks))]
    files.append(("portfolio/main/listing", "info", "set", "listing-info.json"))
    batches, cur, size = [], [], 0
    for col, doc, op, name in files:
        path = os.path.abspath(f"{out}/{name}")
        n = os.path.getsize(path)
        if cur and size + n > 800_000:
            batches.append(cur)
            cur, size = [], 0
        cur.append({"op": op, "collection": col, "doc_id": doc, "file_path": path})
        size += n
    batches.append(cur)
    dump("db-writes.json", {"batches": batches})
    print(json.dumps({"prices": len(prices), "fx": fx, "heat_jp": len(heat["jp"]), "heat_us": len(heat["us"]),
                      "listing": len(items), "watch": len(watch_prices), "batches": len(batches),
                      "errors": errors}, ensure_ascii=False))


if __name__ == "__main__":
    main()
