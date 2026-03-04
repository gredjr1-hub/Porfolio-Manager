import yfinance as yf
import pandas as pd
import numpy as np
import os
import json
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# --- HELPER FUNCTIONS ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calculate_macd(series):
    exp1 = series.ewm(span=12, adjust=False).mean()
    exp2 = series.ewm(span=26, adjust=False).mean()
    macd = exp1 - exp2
    return macd, macd.ewm(span=9, adjust=False).mean()

def calculate_bbands(series, window=20, num_std=2):
    rolling_mean = series.rolling(window=window).mean()
    rolling_std = series.rolling(window=window).std()
    return rolling_mean + (rolling_std * num_std), rolling_mean - (rolling_std * num_std)

# --- MAIN AUTOMATION ---
def run_scan_and_log():
    global_universe = [
        'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSM', 'AVGO', 'NVO', 'JPM', 
        'WMT', 'LLY', 'V', 'PG', 'MA', 'JNJ', 'ASML', 'HD', 'ORCL', 'COST', 
        'CVX', 'BABA', 'CRM', 'AMD', 'BAC', 'PEP', 'LIN', 'KO', 'ADBE', 'DIS', 
        'CSCO', 'TM', 'INTC', 'VZ', 'PFE', 'NKE', 'SHEL', 'AZN', 'NVS', 'SAP', 
        'SNY', 'SONY', 'RY', 'PLTR', 'UBER', 'CRWD', 'PANW', 'ARM', 'SMCI', 'ALB', 'NFLX', 'CVS', 'HOOD', 'IBM', 'IREN', 'LRCX', 'AMAT', 'XOM', 'LMT', 'ZETA', 'ACHR', 'UNH', 'NKE', 'COIN', 'NVO', 'ANET', 'CRSP', 'CRWV', 'DIS', 'DUK', 'GLXY', 'LULU', 'NBIS', 'NRG', 'SBUX', 'TSLA'
    ]

    today_str = datetime.today().strftime('%Y-%m-%d')
    
    # 1. CONNECT TO GOOGLE SHEETS FIRST
    print("Authenticating with Google Sheets to check existing logs...")
    gcp_json = os.environ.get('GCP_SERVICE_ACCOUNT') 
    if not gcp_json:
        print("No Google Credentials found in environment. Exiting.")
        return

    skey = json.loads(gcp_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    credentials = Credentials.from_service_account_info(skey, scopes=scopes)
    gc = gspread.authorize(credentials)
    
    sheet = gc.open("Stock_Quant_History").sheet1
    existing_data = sheet.get_all_records()
    
    # 2. DETERMINE WHICH TICKERS ARE MISSING TODAY
    logged_today = {str(row.get('Ticker', '')) for row in existing_data if str(row.get('Date', '')) == today_str}
    tickers_to_scan = [t for t in global_universe if t not in logged_today]
    
    if not tickers_to_scan:
        print(f"✅ All {len(global_universe)} tickers have already been logged for {today_str}. Exiting to save API calls.")
        return
        
    print(f"Found {len(logged_today)} tickers already logged today.")
    print(f"Fetching Yahoo Finance data for the remaining {len(tickers_to_scan)} missing tickers...")

    # 3. ONLY SCAN MISSING TICKERS
    portfolio_data = []
    for ticker in tickers_to_scan:
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period='2y')
            if hist.empty: continue
            
            current_price = hist['Close'].iloc[-1]
            info = stock.info
            
            # Fetch fundamentals safely
            t_pe = info.get('trailingPE', 'N/A')
            f_pe = info.get('forwardPE', 'N/A')
            peg = info.get('trailingPegRatio', info.get('pegRatio', 'N/A'))
            insiders = info.get('heldPercentInsiders', 'N/A')
            roe = info.get('returnOnEquity', 'N/A')
            margins = info.get('grossMargins', 'N/A')
            fcf = info.get('freeCashflow', 'N/A')
            mkt_cap = info.get('marketCap', 'N/A')
            target = info.get('targetMeanPrice', 'N/A')
            
            fcf_yield = (fcf / mkt_cap * 100) if isinstance(fcf, (int, float)) and isinstance(mkt_cap, (int, float)) and mkt_cap > 0 else 'N/A'
            upside = ((target - current_price) / current_price * 100) if isinstance(target, (int, float)) and target > 0 and current_price > 0 else 'N/A'
            
            # Technicals
            volatility = hist['Close'].tail(252).pct_change().std() * np.sqrt(252) * 100 if len(hist) >= 252 else 0.0
            rsi_series = calculate_rsi(hist['Close'])
            macd_line, signal_line = calculate_macd(hist['Close'])
            upper_b, lower_b = calculate_bbands(hist['Close'])
            
            rsi_14 = round(rsi_series.iloc[-1], 2)
            macd_val, sig_val = macd_line.iloc[-1], signal_line.iloc[-1]
            bb_lower = lower_b.iloc[-1]

            # The Algorithm Math
            score = 50 
            risk_points = 0
            
            if isinstance(roe, (float, int)):
                if roe >= 0.2: score += 5
                elif roe < 0.05: score -= 7; risk_points += 1
            if isinstance(margins, (float, int)):
                if margins >= 0.5: score += 4
                elif margins < 0.10: score -= 4
            if isinstance(peg, (float, int)):
                if peg < .9: score += 5
                elif peg > 2.5: score -= 7; risk_points += 1
            if isinstance(t_pe, (float, int)) and isinstance(f_pe, (float, int)):
                if f_pe < t_pe: score += 5  
                elif f_pe > (t_pe * 1.2): score -= 5 
                if f_pe > 50 or f_pe < 0: risk_points += 1
            if isinstance(fcf_yield, (float, int)):
                if fcf_yield > 6.0: score += 5
                elif fcf_yield < 0: score -= 5; risk_points += 1
            if isinstance(upside, (float, int)):
                if upside > 25: score += 10
                elif upside < 0: score -= 10
            if isinstance(insiders, (float, int)):
                if insiders >= 0.15: score += 10
                elif insiders >= 0.05: score += 5
            if isinstance(rsi_14, (float, int)):
                if rsi_14 < 30: score += 10
                elif rsi_14 > 65: score -= 10
            if isinstance(macd_val, (float, int)) and isinstance(sig_val, (float, int)):
                if macd_val > sig_val: score += 5
                else: score -= 5
            if current_price < bb_lower: score += 8
                
            score = max(0, min(100, int(score))) 
            
            if score >= 85: decision = "ADD 🟩"
            elif score >= 65: decision = "HOLD/ACCUMULATE 🟨"
            elif score >= 40: decision = "HOLD ⬜"
            elif score >= 30: decision = "TRIM 🟧"
            else: decision = "SELL 🟥"

            portfolio_data.append({
                'Ticker': ticker, 'Price': round(current_price, 2), 'Score': score, 
                'Decision': decision, 'Risk_Pts': risk_points, 'ROE': roe, 
                'Margins': margins, 'PEG': peg, 'FCF_Y': fcf_yield, 
                'Insiders': insiders, 'Upside': upside
            })
        except Exception as e:
            print(f"Error processing {ticker}: {e}")

    # 4. UPLOAD THE REMAINDER TO GOOGLE SHEETS
    new_rows = []
    for stock in portfolio_data:
        new_rows.append([
            today_str, stock['Ticker'], stock['Price'], stock['Score'], 
            stock['Decision'], stock['Risk_Pts'], stock['ROE'], 
            stock['Margins'], stock['PEG'], stock['FCF_Y'],
            stock['Insiders'], stock['Upside']
        ])
            
    if new_rows:
        sheet.append_rows(new_rows)
        print(f"✅ Successfully added {len(new_rows)} rows to Google Sheets.")
    else:
        print("No valid data fetched to append.")

if __name__ == "__main__":
    run_scan_and_log()