#!/usr/bin/env python3
"""業種ヒートマップの「業種ごとの代表20社」一覧を作るスクリプト。

日本株: Yahoo!ファイナンスの業種別銘柄一覧（東証33業種）から、時価総額の大きい順に20社。
米国株: 業種ごとのETFと、その業種の代表的な大型株20社（下の US_INDUSTRIES）。

使い方: python3 tools/build_universe.py tools/universe.json
銘柄の入れ替えは頻繁ではないので、月に1回ほど作り直せば十分。
"""
import json
import re
import sys
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

# 米国: 業種名, 業種ETF, 代表20社
US_INDUSTRIES = [
    ("半導体", "SMH", "NVDA AVGO TSM AMD QCOM TXN INTC MU AMAT LRCX KLAC ADI MRVL NXPI MCHP ON MPWR ASML ARM TER"),
    ("ソフトウェア", "IGV", "MSFT ORCL CRM ADBE NOW INTU PANW PLTR SNPS CDNS CRWD ADSK WDAY FTNT DDOG TEAM ROP SNOW HUBS ZS"),
    ("インターネット", "FDN", "AMZN META GOOGL NFLX BKNG UBER ABNB DASH SHOP PYPL EBAY ETSY PINS SNAP CHWY EXPE RBLX SPOT MELI CPNG"),
    ("通信・メディア", "XLC", "GOOGL META NFLX DIS CMCSA T VZ TMUS CHTR EA TTWO WBD LYV OMC FOXA NWSA MTCH TTD ROKU SIRI"),
    ("銀行", "KBE", "JPM BAC WFC C USB PNC TFC MTB FITB HBAN RF KEY CFG FCNCA ZION WAL EWBC FHN WBS CMA"),
    ("証券・資産運用", "IAI", "GS MS SCHW BLK BX KKR APO IBKR RJF LPLA TROW BEN IVZ NTRS STT BK AMP ARES CG HOOD"),
    ("保険", "KIE", "BRK-B PGR CB MMC AON TRV AIG ALL MET PRU AFL HIG AJG WTW CINF L PFG ACGL EG MKL"),
    ("カード・決済", "IPAY", "V MA AXP PYPL FI FIS GPN COF SYF XYZ AFRM TOST WEX JKHY FOUR CPAY SOFI ALLY EEFT DFS"),
    ("医薬品", "IHE", "LLY JNJ MRK ABBV PFE BMY ZTS VTRS JAZZ PRGO NVO AZN NVS GSK SNY TAK ELAN OGN ITCI CORT"),
    ("バイオ", "XBI", "AMGN GILD VRTX REGN BIIB MRNA ALNY ARGX BMRN INCY NBIX SRPT EXEL UTHR INSM ILMN BNTX TECH IONS CRSP"),
    ("医療機器", "IHI", "ABT TMO DHR ISRG SYK MDT BSX BDX EW ZBH GEHC IDXX RMD DXCM STE BAX HOLX PODD ALGN COO"),
    ("ヘルスケアサービス", "IHF", "UNH ELV CI CVS HUM HCA CNC MCK COR CAH MOH UHS DVA LH DGX THC ENSG ACHC OSCR HQY"),
    ("航空宇宙・防衛", "ITA", "GE RTX BA LMT NOC GD LHX TDG HWM HEI TXT AXON LDOS BWXT CW HII KTOS RKLB SPCX WWD"),
    ("機械・資本財", "XLI", "CAT DE HON ETN PH ITW EMR CMI PCAR ROK OTIS CARR JCI TT GWW FAST DOV XYL IR AME"),
    ("運輸", "IYT", "UNP UPS FDX CSX NSC ODFL DAL UAL LUV JBHT CHRW EXPD XPO SAIA KNX LSTR AAL ALK MATX R"),
    ("住宅・建設", "ITB", "DHI LEN PHM NVR TOL HD LOW SHW MAS BLDR MHK OC TREX FND WSM KBH MTH TMHC IBP LII"),
    ("小売", "XRT", "WMT COST TGT TJX ROST DG DLTR BBY ULTA KR AZO ORLY TSCO BURL GAP DKS FIVE KSS M ANF"),
    ("自動車・レジャー", "XLY", "TSLA MCD SBUX NKE CMG MAR HLT YUM RCL CCL LVS WYNN DPZ DRI LULU DECK GM F RIVN NCLH"),
    ("生活必需品", "XLP", "PG KO PEP PM MO MDLZ CL KMB GIS KHC HSY STZ KDP MNST CHD CLX SYY KVUE TSN EL"),
    ("エネルギー", "XLE", "XOM CVX COP EOG SLB OXY PSX MPC VLO WMB KMI OKE FANG DVN HAL BKR TRGP EQT CTRA HES"),
    ("素材・化学", "XLB", "LIN SHW APD ECL DD DOW LYB PPG NUE VMC MLM CTVA IFF ALB CF MOS IP PKG AVY EMN"),
    ("金属・資源", "XME", "FCX NEM NUE STLD CLF AA SCCO B AEM WPM FNV RGLD MP CMC CENX HL CDE KGC GOLD X"),
    ("公益", "XLU", "NEE SO DUK CEG AEP SRE D EXC XEL PCG ED PEG WEC EIX ETR DTE AEE VST NRG CNP"),
    ("不動産", "XLRE", "PLD AMT EQIX WELL SPG PSA O CCI DLR VICI EXR AVB EQR IRM CBRE SBAC WY ARE MAA INVH"),
    ("クリーンエネルギー", "ICLN", "FSLR ENPH SEDG RUN PLUG BE ARRY NXT CSIQ JKS SHLS FLNC GEV ORA BEPC AES CWEN HASI EOSE NEE"),
]


def get(url):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** (attempt + 1))


ITEM = re.compile(r'"code":"([0-9A-Z]{4,5})","marketName":"([^"]*)","flagName":[^,]*,"name":"([^"]+)".*?'
                  r'"totalPrice":\{"value":"([\d,\-]*)"')
TOTAL = re.compile(r'<!-- -->(\d[\d,]*)<!-- -->件中')


def jp_sector(item):
    code, name = item
    first = get(f"https://finance.yahoo.co.jp/search/qi/?ids={code}")
    m = TOTAL.search(first)
    total = int(m.group(1).replace(",", "")) if m else 20
    pages = [first] + [get(f"https://finance.yahoo.co.jp/search/qi/?ids={code}&page={p}")
                       for p in range(2, (total + 19) // 20 + 1)]
    seen = {}
    for html in pages:
        for c, market, n, cap in ITEM.findall(html):
            if c in seen or not cap or cap == "-":
                continue
            seen[c] = {"code": c, "name": n, "market": market, "cap": int(cap.replace(",", ""))}
    top = sorted(seen.values(), key=lambda x: -x["cap"])[:20]
    print(f"{name}: {total}社中 {len(top)}社", file=sys.stderr)
    return {"sector": name, "code": code, "members": top}


def us_check(sym):
    try:
        d = json.loads(get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=5d&interval=1d"))
        meta = d["chart"]["result"][0]["meta"]
        return sym, meta.get("shortName") or meta.get("longName") or sym
    except Exception:
        return sym, None


def main():
    out = sys.argv[1]
    with ThreadPoolExecutor(4) as ex:
        jp = list(ex.map(jp_sector, TSE33))

    syms = sorted({s for _, etf, m in US_INDUSTRIES for s in m.split()} | {etf for _, etf, _ in US_INDUSTRIES})
    with ThreadPoolExecutor(6) as ex:
        names = dict(ex.map(us_check, syms))
    us = []
    for sector, etf, members in US_INDUSTRIES:
        ok = [{"code": s, "name": names[s]} for s in members.split() if names.get(s)]
        dropped = [s for s in members.split() if not names.get(s)]
        if dropped:
            print(f"{sector}: 取得できず除外 {dropped}", file=sys.stderr)
        us.append({"sector": sector, "etf": etf, "etfName": names.get(etf), "members": ok[:20]})

    json.dump({"jp": jp, "us": us}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"jp": len(jp), "jp_members": sum(len(s["members"]) for s in jp),
                      "us": len(us), "us_members": sum(len(s["members"]) for s in us)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
