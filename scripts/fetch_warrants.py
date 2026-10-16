"""抓取台灣上市、上櫃權證盤後資料，計算指標後輸出 data/warrants.json。

資料來源（皆為免費公開資料）：
  1. 證交所 OpenAPI：上市權證基本資料彙總表、上市個股日成交資訊
  2. 櫃買中心 OpenAPI：上櫃權證基本資料彙總表、上櫃權證行情、上櫃個股收盤行情
  3. 證交所每日收盤行情（MI_INDEX，type=0999 認購 / 0999P 認售）：上市權證收盤價與最後揭示買賣價

權證的 API 路徑不寫死，而是在執行時讀取 swagger.json、依中文說明自動找出，
交易所改路徑時比較不容易壞。

用法：
  python scripts/fetch_warrants.py            # 抓真實資料
  python scripts/fetch_warrants.py --demo     # 產生示範資料（離線測試用）
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pricing import bs_price, warrant_metrics  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "warrants.json"
TZ = dt.timezone(dt.timedelta(hours=8))

TWSE_OPENAPI = "https://openapi.twse.com.tw/v1"
TPEX_OPENAPI = "https://www.tpex.org.tw/openapi/v1"
TWSE_SWAGGER = "https://openapi.twse.com.tw/v1/swagger.json"
TPEX_SWAGGER = "https://www.tpex.org.tw/openapi/swagger.json"
TWSE_MI_INDEX = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (tw-warrant-screener; +https://github.com/)",
    "Accept": "application/json, text/plain, */*",
}


def log(*a):
    print("[fetch]", *a, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- HTTP

def get_json(url: str, params: dict | None = None, retries: int = 3):
    import requests  # 只有抓真實資料時才需要

    last = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  第 {i + 1} 次失敗：{url} → {e}")
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"無法取得 {url}: {last}")


def find_paths(swagger_url: str, must: list[str], any_of: list[str] | None = None,
               exclude: list[str] | None = None) -> list[tuple[str, str]]:
    """從 swagger.json 依 summary/description 關鍵字找 API 路徑。"""
    sw = get_json(swagger_url)
    hits = []
    for path, ops in sw.get("paths", {}).items():
        op = ops.get("get") or {}
        text = f"{op.get('summary', '')} {op.get('description', '')}"
        if not all(k in text for k in must):
            continue
        if any_of and not any(k in text for k in any_of):
            continue
        if exclude and any(k in text for k in exclude):
            continue
        hits.append((path, op.get("summary", "")))
    return hits


# ---------------------------------------------------------------- 解析工具

def pick(row: dict, *cands: str):
    """依候選欄位名稱取值：先完全相符，再找包含該字串的欄位。"""
    for c in cands:
        if c in row:
            return row[c]
    for c in cands:
        for k in row:
            if c in k:
                return row[k]
    return None


def num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").replace("+", "").strip()
    s = re.sub(r"<[^>]+>", "", s)  # MI_INDEX 漲跌欄位含 HTML
    if s in ("", "--", "-", "---", "N/A", "除權息", "除權", "除息"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if f != 0 else None


def parse_date(v) -> dt.date | None:
    """支援 115/10/16、1151016、2026/10/16、20261016、2026-10-16。"""
    if not v:
        return None
    s = re.sub(r"[^\d]", "/", str(v).strip()).strip("/")
    parts = [p for p in s.split("/") if p]
    try:
        if len(parts) == 3:
            y, m, d = map(int, parts)
        elif len(parts) == 1 and len(parts[0]) in (7, 8):
            p = parts[0]
            y, m, d = int(p[:-4]), int(p[-4:-2]), int(p[-2:])
        else:
            return None
        if y < 1911:
            y += 1911
        return dt.date(y, m, d)
    except ValueError:
        return None


CODE_RE = re.compile(r"^\s*([0-9A-Z]{4,6})\b")


def split_underlying(v: str) -> tuple[str | None, str]:
    s = str(v or "").strip()
    m = CODE_RE.match(s)
    if m:
        return m.group(1), s[m.end():].strip(" -()（）")
    return None, s


# ---------------------------------------------------------------- 資料抓取

def fetch_basic(market: str) -> list[dict]:
    swagger, base = (TWSE_SWAGGER, TWSE_OPENAPI) if market == "TWSE" else (TPEX_SWAGGER, TPEX_OPENAPI)
    hits = find_paths(swagger, ["權證", "基本資料"], exclude=["牛熊", "發行人", "海外", "議約"])
    if not hits:
        raise RuntimeError(f"{market} swagger 找不到權證基本資料 API")
    path, summary = hits[0]
    log(f"{market} 權證基本資料：{path}（{summary}）")
    return get_json(base + path)


def fetch_twse_underlying(date: dt.date) -> dict[str, tuple[float, str]]:
    """上市股票／ETF 收盤價。

    用和權證行情同一支 MI_INDEX（type=ALLBUT0999）並指定同一天，確保日期一致。
    （OpenAPI 的 STOCK_DAY_ALL 盤後更新較慢，傍晚可能還是前一天的價格。）
    """
    js = get_json(TWSE_MI_INDEX, {"date": date.strftime("%Y%m%d"), "type": "ALLBUT0999", "response": "json"})
    out = {}
    for fields, data in _mi_index_tables(js):
        if not (any("證券代號" in f for f in fields) and any("收盤價" in f for f in fields)):
            continue
        for row in data:
            r = dict(zip(fields, row))
            code, px = str(pick(r, "證券代號") or "").strip(), num(pick(r, "收盤價"))
            if code and px:
                out[code] = (px, str(pick(r, "證券名稱") or "").strip())
    if len(out) < 500:
        raise RuntimeError(f"{date} 上市收盤價只有 {len(out)} 筆，資料可能還沒出齊，稍後再跑")
    log(f"上市標的收盤價（{date}）{len(out)} 筆")
    return out


def fetch_tpex_underlying(date: dt.date) -> dict[str, tuple[float, str]]:
    hits = find_paths(TPEX_SWAGGER, ["收盤"], any_of=["上櫃股票", "上櫃證券"], exclude=["權證", "債", "興櫃"])
    path = hits[0][0] if hits else "/tpex_mainboard_daily_close_quotes"
    rows = get_json(TPEX_OPENAPI + path)
    check_date(rows, date, "上櫃標的收盤價")
    out = {}
    for r in rows:
        code = pick(r, "SecuritiesCompanyCode", "Code", "代號")
        px = num(pick(r, "Close", "ClosingPrice", "收盤"))
        if code and px:
            out[str(code).strip()] = (px, str(pick(r, "CompanyName", "Name", "名稱") or "").strip())
    log(f"上櫃標的收盤價（{path}）{len(out)} 筆")
    return out


def check_date(rows: list[dict], date: dt.date, what: str):
    """資料有日期欄位時，確認和權證行情是同一天，避免新舊價格混用。"""
    dates = {parse_date(pick(r, "Date", "資料日期", "日期")) for r in rows[:50]} - {None}
    if dates and date not in dates:
        got = ", ".join(sorted(str(d) for d in dates))
        raise RuntimeError(f"{what}日期是 {got}，不是 {date}，資料還沒更新，稍後再跑")


def _mi_index_tables(js: dict):
    """相容新舊兩種 MI_INDEX JSON 格式。"""
    if isinstance(js.get("tables"), list):
        for t in js["tables"]:
            yield t.get("fields") or [], t.get("data") or []
    for k, v in js.items():
        if k.startswith("fields"):
            yield v, js.get("data" + k[len("fields"):], [])


def fetch_twse_quotes(date: dt.date) -> dict[str, dict]:
    out = {}
    for typ in ("0999", "0999P"):  # 認購、認售
        js = get_json(TWSE_MI_INDEX, {"date": date.strftime("%Y%m%d"), "type": typ, "response": "json"})
        if js.get("stat") not in (None, "OK"):
            continue
        for fields, data in _mi_index_tables(js):
            if not any("揭示買價" in f for f in fields):
                continue
            for row in data:
                r = dict(zip(fields, row))
                code = str(pick(r, "證券代號") or "").strip()
                if not code:
                    continue
                out[code] = {
                    "bid": num(pick(r, "最後揭示買價")),
                    "ask": num(pick(r, "最後揭示賣價")),
                    "close": num(pick(r, "收盤價")),
                    "vol": num(pick(r, "成交股數")),
                }
        time.sleep(3)  # 證交所有頻率限制
    return out


def fetch_tpex_quotes(date: dt.date) -> dict[str, dict]:
    hits = find_paths(TPEX_SWAGGER, ["權證"], any_of=["行情", "收盤", "成交"],
                      exclude=["牛熊", "基本資料", "發行人", "註銷", "重大"])
    if not hits:
        log("⚠ TPEx swagger 找不到權證行情 API，上櫃權證將沒有報價")
        return {}
    path, summary = hits[0]
    log(f"TPEx 權證行情：{path}（{summary}）")
    rows = get_json(TPEX_OPENAPI + path)
    check_date(rows, date, "上櫃權證行情")
    out = {}
    for r in rows:
        code = str(pick(r, "SecuritiesCompanyCode", "Code", "代號") or "").strip()
        if not code:
            continue
        out[code] = {
            "bid": num(pick(r, "LatestBidPrice", "BidPrice", "Bid", "最後買價", "買價")),
            "ask": num(pick(r, "LatesAskPrice", "LatestAskPrice", "AskPrice", "Ask", "最後賣價", "賣價")),
            "close": num(pick(r, "Close", "ClosingPrice", "收盤")),
            "vol": num(pick(r, "TradingShares", "TradeVolume", "Volume", "成交股數", "成交數量")),
        }
    return out


def latest_twse_quotes() -> tuple[dt.date, dict]:
    today = dt.datetime.now(TZ).date()
    for back in range(0, 10):
        d = today - dt.timedelta(days=back)
        if d.weekday() >= 5:
            continue
        q = fetch_twse_quotes(d)
        if q:
            log(f"上市權證行情日期 {d}，共 {len(q)} 檔")
            return d, q
        log(f"{d} 沒有行情，往前一天")
    raise RuntimeError("近 10 天都抓不到上市權證行情")


# ---------------------------------------------------------------- 組合與計算

FIELDS = ["code", "name", "cp", "mkt", "uCode", "uName", "S", "K", "ratio", "expiry", "days",
          "bid", "ask", "close", "vol", "moneyness", "spread", "iv", "delta", "lev", "sl"]


def ratio_divisor(basic: list[dict]) -> float:
    """判斷行使比例單位：每仟單位（例如 8 → 0.008）或每單位（0.008）。"""
    vals = sorted(v for v in (num(pick(r, "履約配發數量", "行使比例")) for r in basic) if v)
    if not vals:
        return 1000.0
    return 1000.0 if vals[len(vals) // 2] > 1 else 1.0


def build_rows(market: str, basic: list[dict], quotes: dict[str, dict],
               underlying: dict[str, tuple[float, str]], asof: dt.date) -> list[list]:
    div = ratio_divisor(basic)
    name_to_code = {name: code for code, (_, name) in underlying.items() if name}
    rows, skipped = [], 0
    for r in basic:
        code = str(pick(r, "權證代號") or "").strip()
        name = str(pick(r, "權證簡稱") or "").strip()
        wtype = f"{pick(r, '權證類型') or ''}{pick(r, '類別') or ''}"
        if not code or "牛" in wtype or "熊" in wtype:
            continue
        if "售" in wtype or (not ("購" in wtype) and "售" in name):
            is_call = False
        elif "購" in wtype or "購" in name:
            is_call = True
        else:
            continue

        u_code, u_name = split_underlying(pick(r, "標的證券", "標的") or "")
        if not u_code:
            u_code = name_to_code.get(u_name)
        S = underlying.get(u_code, (None, ""))[0] if u_code else None
        if u_code and not u_name:
            u_name = underlying.get(u_code, (None, ""))[1]

        K = num(pick(r, "最新履約價格", "履約價"))
        raw_ratio = num(pick(r, "最新標的履約配發數量", "履約配發數量", "行使比例"))
        ratio = raw_ratio / div if raw_ratio else None
        expiry = parse_date(pick(r, "履約截止日", "到期日")) or parse_date(pick(r, "最後交易日"))
        days = (expiry - asof).days if expiry else None
        if days is not None and days < 0:
            continue

        q = quotes.get(code, {})
        bid, ask, close = q.get("bid"), q.get("ask"), q.get("close")
        if not (S and K and ratio):
            skipped += 1
        m = warrant_metrics(S=S or 0, K=K or 0, ratio=ratio or 0, days=days or 0,
                            is_call=is_call, bid=bid, ask=ask, close=close)
        vol = q.get("vol")
        rows.append([
            code, name, "C" if is_call else "P", "上市" if market == "TWSE" else "上櫃",
            u_code, u_name, S, K, round(ratio, 6) if ratio else None,
            expiry.isoformat() if expiry else None, days,
            bid, ask, close, int(vol / 1000) if vol else 0,
            m["moneyness"], m["spread"], m["iv"], m["delta"], m["lev"], m["sl"],
        ])
    log(f"{market}：{len(rows)} 檔（{skipped} 檔缺標的價/履約價/行使比例，指標留空）")
    return rows


def write_output(rows: list[list], asof: dt.date, demo: bool = False):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "asof": asof.isoformat(),
        "updated": dt.datetime.now(TZ).isoformat(timespec="minutes"),
        "demo": demo,
        "fields": FIELDS,
        "rows": rows,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"已寫入 {OUT}（{len(rows)} 檔，{OUT.stat().st_size / 1024:.0f} KB）")


def run_live():
    asof, tw_quotes = latest_twse_quotes()
    time.sleep(3)
    tw_under = fetch_twse_underlying(asof)
    rows = build_rows("TWSE", fetch_basic("TWSE"), tw_quotes, tw_under, asof)
    try:
        tp_under = fetch_tpex_underlying(asof)
        all_under = {**tw_under, **tp_under}
        rows += build_rows("TPEX", fetch_basic("TPEX"), fetch_tpex_quotes(asof), all_under, asof)
    except Exception as e:  # noqa: BLE001  上櫃失敗不影響上市資料
        log(f"⚠ 上櫃資料失敗，只輸出上市權證：{e}")
    if not any(r[11] or r[12] for r in rows):
        raise RuntimeError("所有權證都沒有買賣價，欄位可能改名，請檢查 log")
    write_output(rows, asof)


# ---------------------------------------------------------------- 示範資料

def make_demo():
    """產生格式與真實 API 相同的假資料，跑過完整流程。"""
    rnd = random.Random(42)
    asof = dt.date(2026, 10, 16)
    stocks = {"2330": ("台積電", 1450.0), "2317": ("鴻海", 215.0), "2454": ("聯發科", 1580.0),
              "2603": ("長榮", 198.0), "3231": ("緯創", 132.0), "0050": ("元大台灣50", 62.5),
              "2382": ("廣達", 318.0), "2308": ("台達電", 905.0)}
    issuers = ["元大", "凱基", "群益", "統一", "富邦", "國泰", "中信", "永豐"]
    under = {c: (p, n) for c, (n, p) in stocks.items()}
    basic, quotes = [], {}
    i = 0
    for ucode, (uname, S) in stocks.items():
        for _ in range(40):
            i += 1
            is_call = rnd.random() < 0.7
            K = round(S * rnd.uniform(0.8, 1.3 if is_call else 1.1), 2)
            days = rnd.randint(5, 300)
            ratio = rnd.choice([0.002, 0.005, 0.008, 0.01, 0.02, 0.05, 0.1, 0.2])
            if S < 300:
                ratio *= 5
            ratio = round(ratio, 3)
            sigma = rnd.uniform(0.3, 0.6)
            fair = bs_price(S, K, days / 365, sigma, is_call) * ratio
            if fair < 0.05:
                continue
            tick = 0.01 if fair < 5 else 0.05
            mid = round(fair / tick) * tick
            spread_ticks = rnd.choice([1, 1, 1, 2, 3, 5])
            bid = round(mid - tick * (spread_ticks // 2 or 0), 2)
            ask = round(bid + tick * spread_ticks, 2)
            exp = asof + dt.timedelta(days=days)
            iss = rnd.choice(issuers)
            code = f"{'0' if is_call else '7'}{i:05d}"
            roc = f"{exp.year - 1911}/{exp.month:02d}/{exp.day:02d}"
            basic.append({
                "出表日期": "1151016", "權證代號": code,
                "權證簡稱": f"{uname}{iss}{(exp.year - 1911) % 10}{'123456789ABC'[exp.month - 1]}"
                            f"{'購' if is_call else '售'}{i % 99:02d}",
                "權證類型": "認購權證" if is_call else "認售權證", "類別": "一般型",
                "履約開始日": roc, "最後交易日": roc, "履約截止日": roc,
                "標的證券/指數": f"{ucode} {uname}",
                "最新標的履約配發數量(每仟單位權證)": f"{ratio * 1000:.3f}",
                "最新履約價格(元)/履約指數": f"{K:.2f}",
            })
            quotes[code] = {"bid": bid if bid > 0 else None, "ask": ask, "close": round(mid, 2),
                            "vol": rnd.choice([0, 0, 1000, 5000, 20000, 150000])}
    rows = build_rows("TWSE", basic, quotes, under, asof)
    write_output(rows, asof, demo=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="產生示範資料（不連網）")
    args = ap.parse_args()
    make_demo() if args.demo else run_live()
