import datetime as dt
import json
import os
import pandas as pd
import requests
from pykrx import stock
from dotenv import load_dotenv

# =============================
# 텔레그램 설정
# =============================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
load_dotenv(dotenv_path=".env")

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    requests.post(url, data=payload, timeout=10)

# =============================
# 전략 설정
# =============================
MARKET = "ALL"
TOP_LIQUIDITY = 300
LOOKBACK_DAYS = 800

VOLUME_MULT = 1.5
BREAKOUT_LOOKBACK = 20
MA50_SLOPE_LOOKBACK = 20
EXIT_NDAY_LOW = 10

STATE_FILE = "bb_state.json"

# =============================
# 상태 로드/저장
# =============================
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"positions": {}}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# =============================
# 지표 계산
# =============================
def add_indicators(df):
    df["ma20"] = df["종가"].rolling(20).mean()
    df["ma50"] = df["종가"].rolling(50).mean()
    df["ma200"] = df["종가"].rolling(200).mean()
    df["vol_ma20"] = df["거래량"].rolling(20).mean()

    ma = df["종가"].rolling(20).mean()
    sd = df["종가"].rolling(20).std(ddof=0)
    df["bb_up"] = ma + 2 * sd
    df["bb_mid"] = ma

    df["hh"] = df["종가"].rolling(BREAKOUT_LOOKBACK).max()
    df["ll_exit"] = df["종가"].rolling(EXIT_NDAY_LOW).min()

    return df

# =============================
# 매수 신호 (질 강화)
# =============================
def buy_signal(df):
    if len(df) < 250:
        return False

    t = df.iloc[-1]
    y = df.iloc[-2]

    if pd.isna(t["ma200"]) or pd.isna(t["ma50"]) or pd.isna(t["vol_ma20"]):
        return False

    cond_trend = t["종가"] > t["ma200"]

    past = df.iloc[-1 - MA50_SLOPE_LOOKBACK]
    cond_ma50_up = t["ma50"] > past["ma50"]

    cond_break = (y["종가"] <= y["bb_up"]) and (t["종가"] > t["bb_up"])
    cond_hh = t["종가"] >= t["hh"]
    cond_vol = t["거래량"] >= t["vol_ma20"] * VOLUME_MULT

    return cond_trend and cond_ma50_up and cond_break and cond_hh and cond_vol

# =============================
# 매도/주의 신호
# =============================
def sell_signal(df):
    t = df.iloc[-1]
    y = df.iloc[-2]

    if pd.isna(t["ll_exit"]) or pd.isna(t["ma20"]):
        return None

    # 청산: 10일 최저 종가 이탈
    if t["종가"] < df["ll_exit"].iloc[-2]:
        return "EXIT"

    # 주의: 20MA 이탈
    if y["종가"] >= y["ma20"] and t["종가"] < t["ma20"]:
        return "WARN"

    return None

# =============================
# 메인 실행
# =============================
def main():
    today = dt.datetime.now().strftime("%Y%m%d")
    state = load_state()
    positions = state["positions"]

    cap = stock.get_market_cap_by_ticker(today, market=MARKET)
    cap = cap.sort_values("거래대금", ascending=False).head(TOP_LIQUIDITY)
    tickers = cap.index.tolist()

    buy_list = []
    warn_list = []
    exit_list = []

    for code in tickers:
        try:
            df = stock.get_market_ohlcv_by_date(
                fromdate=(dt.datetime.now() - dt.timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d"),
                todate=today,
                ticker=code
            )

            if df.empty:
                continue

            df = add_indicators(df)

            name = stock.get_market_ticker_name(code)

            # 매수 신호
            if buy_signal(df) and code not in positions:
                buy_list.append(f"{name}({code})")
                positions[code] = {"entry_date": today}

            # 보유 중이면 매도 체크
            if code in positions:
                sig = sell_signal(df)
                if sig == "WARN":
                    warn_list.append(f"{name}({code})")
                elif sig == "EXIT":
                    exit_list.append(f"{name}({code})")
                    del positions[code]

        except Exception:
            continue

    messages = []

    if buy_list:
        messages.append("📈 매수 후보\n" + "\n".join(buy_list))
    if warn_list:
        messages.append("⚠️ 주의 (20MA 이탈)\n" + "\n".join(warn_list))
    if exit_list:
        messages.append("📉 청산 (10일 최저 이탈)\n" + "\n".join(exit_list))
    if not messages:
        messages.append("✅ 오늘은 신호 없음")

    for m in messages:
        send_telegram(m)

    state["positions"] = positions
    save_state(state)

if __name__ == "__main__":
    main()
