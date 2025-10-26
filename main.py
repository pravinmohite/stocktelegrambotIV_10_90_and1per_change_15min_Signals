import os
import numpy as np
import requests
import yfinance as yf
import pandas as pd
from typing import Optional, Tuple

BOT_TOKEN = '7745153783:AAHYmV0ZPdU6reeiwv3nrMO2fS_naQoJ10w'
CHAT_ID = '806642925'

# Define index symbols
indices = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK"}

# Add a history dict to store last 30 ATM IVs for percentile calculation
iv_history = {"NIFTY": [], "BANKNIFTY": []}

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept":
    "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive"
}

NSE_OC_URLS = {
    "NIFTY":
    "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY",
    "BANKNIFTY":
    "https://www.nseindia.com/api/option-chain-indices?symbol=BANKNIFTY"
}

def fetch_nse_option_chain(symbol: str, retries: int = 3):
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)
    for i in range(retries):
        try:
            resp = session.get(NSE_OC_URLS[symbol], headers=HEADERS, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
    return None


def calculate_iv_percentile_sensibull(
        symbol: str) -> Tuple[Optional[float], Optional[float]]:
    """Fetch ATM IV from NSE option chain and calculate percentile like Sensibull"""
    session = requests.Session()
    # Initial request to set cookies
    session.get("https://www.nseindia.com", headers=HEADERS, timeout=5)

    try:
        data = fetch_nse_option_chain(symbol)
        if data is None:
            raise Exception("Failed to fetch NSE option chain")
        spot = data["records"]["underlyingValue"]
        chain_data = data["records"]["data"]

        strikes, ivs = [], []
        for item in chain_data:
            if "CE" in item and "PE" in item:
                ce_iv = item["CE"]["impliedVolatility"]
                pe_iv = item["PE"]["impliedVolatility"]
                strikes.append(item["strikePrice"])
                ivs.append(np.mean([ce_iv, pe_iv]))

        if not ivs:
            return None, None

        # Find ATM strike
        atm_strike = min(strikes, key=lambda x: abs(x - spot))
        atm_iv = ivs[strikes.index(atm_strike)]

        # Maintain last 30 ATM IVs for percentile
        iv_list = iv_history[symbol]
        iv_list.append(atm_iv)
        if len(iv_list) > 30:
            iv_list.pop(0)

        percentile = np.sum(np.array(iv_list) < atm_iv) / len(iv_list) * 100
        return atm_iv, round(percentile, 2)

    except Exception as e:
        print(f"Error fetching IV for {symbol}: {e}")
        return None, None


# Function to calculate implied volatility percentile (approximated via historical vol)
def calculate_iv_percentile(
        symbol: str) -> Tuple[Optional[float], Optional[float]]:
    data = yf.download(symbol,
                       period="1y",
                       interval="1d",
                       progress=False,
                       auto_adjust=True)

    if data is None or data.empty:
        return None, None

    data["Returns"] = data["Close"].pct_change()
    data["Volatility"] = data["Returns"].rolling(20).std() * (252**0.5)
    current_vol = data["Volatility"].iloc[-1]

    if pd.isna(current_vol):
        return None, None

    iv_percentile = (data["Volatility"]
                     < current_vol).sum() / data["Volatility"].count() * 100
    return float(iv_percentile), float(current_vol)


# Main function to analyze both indices
def analyze_indices():
    messages = []
    no_move_data = []  # to collect info for indices without alerts

    for name, symbol in indices.items():
        df = yf.download(symbol,
                         period="2d",
                         interval="1d",
                         progress=False,
                         auto_adjust=True)

        if df is None or len(df) < 2:
            continue

        open_price = df["Open"].iloc[-1].item()
        close_price = df["Close"].iloc[-1].item()
        percent_move = ((close_price - open_price) / open_price) * 100
        #  iv_percentile, _ = calculate_iv_percentile(symbol)
        iv_percentile, _ = calculate_iv_percentile_sensibull(
            "NIFTY" if name == "NIFTY" else "BANKNIFTY")

        # Alert conditions
        move_alert = abs(percent_move) >= 1
        iv_alert = iv_percentile is not None and (iv_percentile <= 10
                                                  or iv_percentile >= 90)
        # Always show summary line (even if no alert)
        iv_percentile_str = f"{iv_percentile:.2f}" if iv_percentile is not None else "N/A"
        print(
            f"ℹ️ {name}: Move={percent_move:.2f}%, IV Percentile={iv_percentile_str}"
        )

        # Generate alerts
        if move_alert or iv_alert:
            msg = f"📊 {name} Index Update:\n"
            msg += f"• Move: {percent_move:.2f}%\n"
            msg += f"• Current Price: {close_price:.2f}\n"
            if iv_percentile is not None:
                msg += f"• IV Percentile: {iv_percentile:.2f}\n"

            if move_alert and abs(percent_move) >= 1:
                msg += "⚡ Significant Move Detected (±1% or more)\n"
            if iv_percentile is not None:
                if iv_percentile <= 10:
                    msg += "🟢 Very Low IV Percentile (≤10)\n"
                elif iv_percentile >= 90:
                    msg += "🔴 Very High IV Percentile (≥90)\n"

            messages.append(msg)
            print(f"✅ {name} triggered alert:\n{msg}\n")
        else:
            no_move_data.append(
                f"❌ {name}: No major move ({percent_move:.2f}%) or IV alert ({iv_percentile_str})."
            )

    # If no alerts triggered at all
    if not messages:
        combined = "\n".join(no_move_data)
        messages.append("✅ No major moves today:\n" + combined)
    else:
        # Also include no-move info below alerts, if any
        if no_move_data:
            messages.append("ℹ️ Other indices summary:\n" +
                            "\n".join(no_move_data))

    return messages


def send_telegram(msg: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(
            "⚠️  BOT_TOKEN or CHAT_ID not set. Skipping Telegram notification."
        )
        print(f"Message that would have been sent:\n{msg}")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(url, data={"chat_id": CHAT_ID, "text": msg})
    if response.status_code != 200:
        print("❌ Telegram Error:", response.text)
    else:
        print("✅ Message sent successfully!")


if __name__ == "__main__":
    alerts = analyze_indices()
    message = "📈 Nifty & BankNifty IV and Move Alerts:\n\n" + "\n\n".join(
        alerts)
    send_telegram(message)
