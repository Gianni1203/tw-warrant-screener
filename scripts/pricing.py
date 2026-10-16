"""Black-Scholes 定價、隱含波動率與權證指標計算。

所有價格以「每 1 單位權證」為準：
    權證價格 = 行使比例 × 每股選擇權價格
"""
from __future__ import annotations

import math

RISK_FREE = 0.015  # 無風險利率（年化），可依需要調整


def _ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def bs_price(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = RISK_FREE) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if is_call else max(K - S, 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    if is_call:
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def bs_delta(S: float, K: float, T: float, sigma: float, is_call: bool, r: float = RISK_FREE) -> float:
    if T <= 0 or sigma <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    return _ncdf(d1) if is_call else _ncdf(d1) - 1.0


def implied_vol(price: float, S: float, K: float, T: float, is_call: bool,
                r: float = RISK_FREE, lo: float = 0.01, hi: float = 5.0) -> float | None:
    """二分法反推隱含波動率；價格低於內含價值或無解時回傳 None。"""
    if price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    if price <= bs_price(S, K, T, lo, is_call, r) or price >= bs_price(S, K, T, hi, is_call, r):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, mid, is_call, r) > price:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-5:
            break
    return 0.5 * (lo + hi)


def warrant_metrics(*, S: float, K: float, ratio: float, days: int, is_call: bool,
                    bid: float | None, ask: float | None, close: float | None) -> dict:
    """計算單一權證的各項指標。

    回傳欄位（無法計算者為 None）：
        moneyness  價內外 %（價內為正、價外為負）
        spread     買賣價差比 %  = (賣價 - 買價) / 賣價
        iv         隱含波動率 %
        delta      Delta
        lev        實際槓桿 = |Delta| × 標的價 × 行使比例 / 權證價
        sl         差槓比 = 價差比(%) / 實際槓桿
    """
    out = {"moneyness": None, "spread": None, "iv": None, "delta": None, "lev": None, "sl": None}
    if S and K:
        m = (S - K) / K if is_call else (K - S) / K
        out["moneyness"] = round(m * 100, 2)

    if bid and ask and ask > 0 and ask >= bid:
        out["spread"] = round((ask - bid) / ask * 100, 3)

    # 計算 IV 用的價格：有買賣價用中價，否則用賣價，再不行用收盤價
    if bid and ask:
        px = (bid + ask) / 2
    else:
        px = ask or close
    if not (px and S and K and ratio and days and days > 0):
        return out

    T = days / 365.0
    sigma = implied_vol(px / ratio, S, K, T, is_call)
    if sigma is None:
        return out
    delta = bs_delta(S, K, T, sigma, is_call)
    # 實際槓桿以「買進成本」計：有賣價用賣價
    cost = ask or px
    lev = abs(delta) * S * ratio / cost
    out["iv"] = round(sigma * 100, 2)
    out["delta"] = round(delta, 4)
    out["lev"] = round(lev, 3)
    if out["spread"] is not None and lev > 0:
        out["sl"] = round(out["spread"] / lev, 4)
    return out
