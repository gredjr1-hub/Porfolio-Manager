import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import io
import re
from datetime import timedelta
import base64
import os
import csv
from datetime import datetime

# --- SESSION STATE FOR AUDIO ---
if 'startup_sound_played' not in st.session_state:
    st.session_state.startup_sound_played = False

st.set_page_config(page_title="Quant Command Center", layout="wide", page_icon="📈")

# --- DATA LOADERS & MULTI-DEVICE LOGGING ---
@st.cache_data(ttl=60)
def load_score_history():
    """Loads the historical quant scores, preferring Google Sheets and falling back to CSV."""
    try:
        # Check if secrets exist for Google Cloud
        if "gcp_service_account" in st.secrets:
            import gspread
            from google.oauth2.service_account import Credentials
            
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            skey = dict(st.secrets["gcp_service_account"])
            credentials = Credentials.from_service_account_info(skey, scopes=scopes)
            gc = gspread.authorize(credentials)
            
            # Pull from Google Sheet
            sheet = gc.open("Stock_Quant_History").sheet1
            data = sheet.get_all_records()
            if data:
                df = pd.DataFrame(data)
                df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
                return df
    except Exception:
        pass # Fail silently and fallback to local CSV

    if os.path.exists("historical_quant_scores.csv"):
        try:
            df = pd.read_csv("historical_quant_scores.csv")
            df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
            return df
        except Exception: pass
        
    return pd.DataFrame()

# --- ALGORITHMIC HELPER FUNCTIONS ---
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
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

def calculate_bbands(series, window=20, num_std=2):
    rolling_mean = series.rolling(window=window).mean()
    rolling_std = series.rolling(window=window).std()
    upper = rolling_mean + (rolling_std * num_std)
    lower = rolling_mean - (rolling_std * num_std)
    return upper, lower

def play_startup_sound():
    if not st.session_state.startup_sound_played:
        audio_file = "startup.mp3" 
        if os.path.exists(audio_file):
            with open(audio_file, "rb") as f:
                audio_bytes = f.read()
            
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            audio_html = f"""
                <audio autoplay="true" style="display:none;">
                    <source src="data:audio/mp3;base64,{audio_base64}" type="audio/mp3">
                </audio>
            """
            st.markdown(audio_html, unsafe_allow_html=True)
            st.session_state.startup_sound_played = True

# --- FORWARD LOGGING DATABASE ---
def log_scores(portfolio_data):
    """Logs scores to Google Sheets if configured, otherwise safely falls back to local CSV."""
    today_str = datetime.today().strftime('%Y-%m-%d')
    
    # 1. Try Google Sheets First
    try:
        if "gcp_service_account" in st.secrets:
            import gspread
            from google.oauth2.service_account import Credentials
            
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            skey = dict(st.secrets["gcp_service_account"])
            credentials = Credentials.from_service_account_info(skey, scopes=scopes)
            gc = gspread.authorize(credentials)
            
            sheet = gc.open("Stock_Quant_History").sheet1
            existing_data = sheet.get_all_records()
            existing_records = set((str(row.get('Date', '')), str(row.get('Ticker', ''))) for row in existing_data)
            
            new_rows = []
            for stock in portfolio_data:
                record_key = (today_str, stock['Ticker'])
                if record_key not in existing_records:
                    new_rows.append([
                        today_str, stock['Ticker'], round(stock['Price'], 2), stock['Score'], 
                        stock['Decision'], stock['Risk_Pts'], stock.get('ROE', 'N/A'), 
                        stock.get('Margins', 'N/A'), stock.get('PEG', 'N/A'), stock.get('FCF_Y', 'N/A'),
                        stock.get('Insiders', 'N/A'), stock.get('Upside', 'N/A')
                    ])
                    
            if new_rows:
                sheet.append_rows(new_rows)
            return # Exit function if GSheets succeeds
    except Exception:
        pass # If anything fails, drop down to local CSV fallback
        
    # 2. Local CSV Fallback
    filename = "historical_quant_scores.csv"
    file_exists = os.path.isfile(filename)
    existing_records = set()
    
    if file_exists:
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader: existing_records.add((row['Date'], row['Ticker']))
        except Exception: pass
            
    with open(filename, 'a', newline='', encoding='utf-8') as f:
        fieldnames = ['Date', 'Ticker', 'Price', 'Score', 'Decision', 'Risk_Pts', 'ROE', 'Margins', 'PEG', 'FCF_Y', 'Insiders', 'Upside']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists: writer.writeheader()
        for stock in portfolio_data:
            if (today_str, stock['Ticker']) not in existing_records:
                writer.writerow({
                    'Date': today_str, 'Ticker': stock['Ticker'], 'Price': round(stock['Price'], 2), 
                    'Score': stock['Score'], 'Decision': stock['Decision'], 'Risk_Pts': stock['Risk_Pts'], 
                    'ROE': stock.get('ROE', 'N/A'), 'Margins': stock.get('Margins', 'N/A'), 
                    'PEG': stock.get('PEG', 'N/A'), 'FCF_Y': stock.get('FCF_Y', 'N/A'), 
                    'Insiders': stock.get('Insiders', 'N/A'), 'Upside': stock.get('Upside', 'N/A')
                })

# --- SESSION STATE FOR WATCHLIST ---
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = []

# --- SIDEBAR & FILE UPLOAD ---
st.sidebar.header("📁 Manage Portfolio")
st.sidebar.markdown("Upload your raw **Fidelity positions CSV**, or a standard three-column CSV.")

uploaded_file = st.sidebar.file_uploader("Upload CSV", type=["csv"])
portfolio = {}

hide_dollars = st.sidebar.toggle("🙈 Hide Dollar Values", value=False)

if uploaded_file is not None:
    bytes_data = uploaded_file.getvalue()
    try:
        raw_text = bytes_data.decode('utf-8', errors='ignore')
        if '"The data and information' in raw_text:
            raw_text = raw_text.split('"The data and information')[0]
            
        df_upload = pd.read_csv(io.StringIO(raw_text), on_bad_lines='skip', index_col=False)
    except Exception as e:
        st.sidebar.error("Error reading file. Please check format.")
        df_upload = pd.DataFrame()

    if 'Account Number' in df_upload.columns and 'Symbol' in df_upload.columns:
        df_upload = df_upload.dropna(subset=['Symbol', 'Quantity']) 
        for index, row in df_upload.iterrows():
            ticker = str(row['Symbol']).replace('**', '').strip().upper()
            if not ticker or ticker == 'NAN' or 'SPAXX' in ticker: continue
            try:
                qty = float(str(row['Quantity']).replace(',', ''))
                
                cost_raw = str(row.get('Average Cost Basis', '0'))
                cost_clean = re.sub(r'[^\d.-]', '', cost_raw)
                avg_cost = float(cost_clean) if cost_clean else 0.0
                
                if ticker in portfolio:
                    prev_shares = portfolio[ticker]['shares']
                    prev_avg = portfolio[ticker]['avg_price']
                    new_shares = prev_shares + qty
                    new_avg = ((prev_shares * prev_avg) + (qty * avg_cost)) / new_shares if new_shares > 0 else 0
                    portfolio[ticker]['shares'] = new_shares
                    portfolio[ticker]['avg_price'] = new_avg
                else:
                    portfolio[ticker] = {'shares': qty, 'avg_price': avg_cost, 'pct_acct': 0.0, 'gl_pct': 0.0}
            except: continue
        st.sidebar.success("✅ Fidelity Portfolio loaded successfully!")
        play_startup_sound()
        
    elif 'Ticker' in df_upload.columns and 'Shares' in df_upload.columns:
        for index, row in df_upload.iterrows():
            ticker = str(row['Ticker']).strip().upper()
            if not ticker or ticker == 'NAN': continue
            try:
                qty_raw = str(row['Shares']).replace(',', '')
                qty = float(qty_raw) if qty_raw else 0.0
                
                cost_raw = str(row.get('Avg_Price', '0'))
                cost_clean = re.sub(r'[^\d.-]', '', cost_raw)
                avg_cost = float(cost_clean) if cost_clean else 0.0
                
                if ticker in portfolio:
                    prev_shares = portfolio[ticker]['shares']
                    prev_avg = portfolio[ticker]['avg_price']
                    new_shares = prev_shares + qty
                    new_avg = ((prev_shares * prev_avg) + (qty * avg_cost)) / new_shares if new_shares > 0 else 0
                    portfolio[ticker]['shares'] = new_shares
                    portfolio[ticker]['avg_price'] = new_avg
                else:
                    portfolio[ticker] = {'shares': qty, 'avg_price': avg_cost, 'pct_acct': 0.0, 'gl_pct': 0.0}
            except: continue
        st.sidebar.success("✅ Standard Portfolio loaded successfully!")
        play_startup_sound()

# --- QUANT DATA FETCHING ENGINE ---
@st.cache_data(ttl=3600) 
def get_portfolio_data(port_dict):
    if not port_dict: return [], {}, 0 
    
    portfolio_data = []
    all_histories = {} 
    total_value = 0

    for ticker, data in port_dict.items():
        shares, avg_price = data.get('shares', 0), data.get('avg_price', 0)
        stock = yf.Ticker(ticker)
        
        hist = stock.history(period='max')
        if not hist.empty:
            current_price = hist['Close'].iloc[-1]
            hist.index = hist.index.tz_localize(None)
            all_histories[ticker] = hist
        else:
            current_price = 0.0
            
        try:
            info = stock.info
            t_pe = info.get('trailingPE', 'N/A')
            f_pe = info.get('forwardPE', 'N/A')
            peg = info.get('trailingPegRatio', info.get('pegRatio', 'N/A'))
            insiders = info.get('heldPercentInsiders', 'N/A')
            
            roe = info.get('returnOnEquity', 'N/A')
            margins = info.get('grossMargins', 'N/A')
           
            fcf = info.get('freeCashflow', 'N/A')
            mkt_cap = info.get('marketCap', 'N/A')
            target = info.get('targetMeanPrice', 'N/A')
            sector = info.get('sector', 'Unknown')
            country = info.get('country', 'Unknown')
            
            fcf_yield = (fcf / mkt_cap * 100) if isinstance(fcf, (int, float)) and isinstance(mkt_cap, (int, float)) and mkt_cap > 0 else 'N/A'
            upside = ((target - current_price) / current_price * 100) if isinstance(target, (int, float)) and target > 0 and current_price > 0 else 'N/A'
        except:
            t_pe, f_pe, peg, insiders, roe, margins, fcf_yield, target, upside, sector, country = 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'Unknown', 'Unknown'
            
        volatility = 0.0
        rsi_14, macd_val, sig_val, bb_upper, bb_lower = 'N/A', 'N/A', 'N/A', 'N/A', 'N/A'
        vol_surge = False
        
        if not hist.empty:
            hist['200_WMA'] = hist['Close'].rolling(window=1000).mean()
            hist['50_SMA'] = hist['Close'].rolling(window=50).mean()
            hist['200_SMA'] = hist['Close'].rolling(window=200).mean()
            if len(hist) >= 252:
                volatility = hist['Close'].tail(252).pct_change().std() * np.sqrt(252) * 100
            
            if len(hist) > 50:
                rsi_series = calculate_rsi(hist['Close'])
                macd_line, signal_line = calculate_macd(hist['Close'])
                upper_b, lower_b = calculate_bbands(hist['Close'])
                
                rsi_14 = round(rsi_series.iloc[-1], 2)
                macd_val, sig_val = macd_line.iloc[-1], signal_line.iloc[-1]
                bb_upper, bb_lower = upper_b.iloc[-1], lower_b.iloc[-1]
                
                avg_vol = hist['Volume'].tail(50).mean()
                if hist['Volume'].iloc[-1] > (avg_vol * 1.5): vol_surge = True
        
        pc_ratio = 'N/A'
        try:
            options = stock.options
            if options:
                opt = stock.option_chain(options[0])
                calls_vol, puts_vol = opt.calls['volume'].sum(), opt.puts['volume'].sum()
                if calls_vol > 0: pc_ratio = round(puts_vol / calls_vol, 2)
        except: pass

        # --- THE ALGORITHM ---
        score = 50 
        risk_points = 0
        breakdown = ["**Base Score:** 50 pts"]
        
        if isinstance(roe, (float, int)):
            if roe >= 0.2: 
                score += 5
                breakdown.append(f"✅ **ROE > 20%:** +5 pts (Strong Capital Efficiency)")
            elif roe < 0.05: 
                score -= 7; risk_points += 1
                breakdown.append(f"❌ **ROE < 5%:** -7 pts (Poor Capital Efficiency) [+1 Risk]")
                
        if isinstance(margins, (float, int)):
            if margins >= 0.5:
                score += 4
                breakdown.append(f"✅ **Gross Margins > 50%:** +4 pts (High Pricing Power)")
            elif margins < 0.10:
                score -= 4
                breakdown.append(f"❌ **Gross Margins < 10%:** -4 pts (Low Pricing Power)")

        if isinstance(peg, (float, int)):
            if peg < .9: 
                score += 5
                breakdown.append("✅ **PEG < .9:** +5 pts (Undervalued growth)")
            elif peg > 2.5: 
                score -= 7; risk_points += 1
                breakdown.append("❌ **PEG > 2.5:** -7 pts (Overvalued) [+1 Risk]")
                
        if isinstance(t_pe, (float, int)) and isinstance(f_pe, (float, int)):
            if f_pe < t_pe: 
                score += 5  
                breakdown.append("✅ **Forward P/E < Trailing:** +5 pts (Earnings expanding)")
            elif f_pe > (t_pe * 1.2): 
                score -= 5 
                breakdown.append("❌ **Forward P/E > Trailing:** -5 pts (Earnings contracting)")
            if f_pe > 50 or f_pe < 0: 
                risk_points += 1
                breakdown.append("⚠️ **Extreme P/E Valuation:** [+1 Risk]")
        elif t_pe == 'N/A' and f_pe == 'N/A':
            risk_points += 1 
            breakdown.append("⚠️ **Missing Earnings Data:** [+1 Risk]")
            
        if isinstance(fcf_yield, (float, int)):
            if fcf_yield > 6.0: 
                score += 5
                breakdown.append("✅ **FCF Yield > 6%:** +5 pts (Strong cash generation)")
            elif fcf_yield < 0: 
                score -= 5; risk_points += 1
                breakdown.append("❌ **Negative FCF Yield:** -5 pts (Cash burn) [+1 Risk]")
            
        if isinstance(upside, (float, int)):
            if upside > 25: 
                score += 10
                breakdown.append(f"✅ **Analyst Upside > 25%:** +10 pts")
            elif upside < 0: 
                score -= 10
                breakdown.append(f"❌ **Analyst Upside Negative:** -10 pts")
            
        if isinstance(insiders, (float, int)):
            if insiders >= 0.15: 
                score += 10
                breakdown.append("✅ **Insiders > 15%:** +10 pts (Massive conviction)")
            elif insiders >= 0.05: 
                score += 5
                breakdown.append("✅ **Insiders > 5%:** +5 pts (Strong conviction)")
            
        if isinstance(rsi_14, (float, int)):
            if rsi_14 < 30: 
                score += 10
                breakdown.append("✅ **RSI < 30:** +10 pts (Oversold/Value Zone)")
            elif rsi_14 > 65: 
                score -= 10
                breakdown.append("❌ **RSI > 65:** -10 pts (Overbought/Exhausted)")
                
        if isinstance(macd_val, (float, int)) and isinstance(sig_val, (float, int)):
            if macd_val > sig_val: 
                score += 5
                breakdown.append("✅ **MACD Bullish Cross:** +5 pts")
            else: 
                score -= 5
                breakdown.append("❌ **MACD Bearish Cross:** -5 pts")
                
        if isinstance(bb_upper, (float, int)) and current_price > 0:
            if current_price < bb_lower: 
                score += 8
                breakdown.append("✅ **Price below Lower BB:** +8 pts (Mean reversion bounce)")
            elif current_price > bb_upper: 
                score -= 8
                breakdown.append("❌ **Price above Upper BB:** -8 pts (Overextended)")
                
        if vol_surge and (hist['Close'].iloc[-1] > hist['Open'].iloc[-1]): 
            score += 5 
            breakdown.append("✅ **Bullish Volume Surge:** +5 pts (Institutional buying)")
            
        if isinstance(pc_ratio, (float, int)):
            if pc_ratio < 0.6: 
                score += 5
                breakdown.append("✅ **Put/Call < 0.6:** +5 pts (Bullish options flow)")
            elif pc_ratio > 1.3: 
                score -= 5
                breakdown.append("❌ **Put/Call > 1.3:** -5 pts (Bearish options flow)")
            
        if volatility > 60: 
            risk_points += 2
            breakdown.append("⚠️ **High Volatility (>60%):** [+2 Risk]")
        elif volatility < 20: 
            risk_points -= 1
            breakdown.append("🛡️ **Low Volatility (<20%):** [-1 Risk]")

        score = max(0, min(100, int(score))) 
        breakdown.append(f"---\n🎯 **Final Quant Score: {score}/100**")
        
        if score >= 85: decision, d_color = "ADD 🟩", "#28a745"
        elif score >= 65: decision, d_color = "HOLD/ACCUMULATE 🟨", "#17a2b8"
        elif score >= 40: decision, d_color = "HOLD ⬜", "#6c757d"
        elif score >= 30: decision, d_color = "TRIM 🟧", "#fd7e14"
        else: decision, d_color = "SELL 🟥", "#dc3545"
            
        if risk_points >= 3: risk_lvl, r_color = "HIGH ⚠️", "red"
        elif risk_points <= 0: risk_lvl, r_color = "LOW 🛡️", "green"
        else: risk_lvl, r_color = "MODERATE ⚖️", "orange"

        val = current_price * shares
        total_value += val
        
        portfolio_data.append({
            'Ticker': ticker, 'Val': val, 'Price': current_price, 'Shares': shares, 'Avg': avg_price,
            'Sector': sector, 'Country': country, 'T_PE': t_pe, 'F_PE': f_pe, 'PEG': peg, 
            'ROE': roe, 'Margins': margins, 'Insiders': insiders, 
            'Target': target, 'Upside': upside, 'FCF_Y': fcf_yield,
            'RSI': rsi_14, 'MACD': macd_val, 'MACD_Sig': sig_val, 'PC_Ratio': pc_ratio, 'Vol': volatility,
            'Score': score, 'Decision': decision, 'D_Color': d_color, 'Risk': risk_lvl, 'R_Color': r_color, 'Risk_Pts': risk_points,
            'Upper_BB': bb_upper, 'Lower_BB': bb_lower,
            'pct_acct': data.get('pct_acct', 0.0), 'gl_pct': data.get('gl_pct', 0.0),
            'Breakdown': breakdown
        })

    # Updated logging call
    log_scores(portfolio_data)

    return portfolio_data, all_histories, total_value

# --- UI HELPER FUNCTION ---
def draw_stock_row(stock, histories, today_date, is_watchlist=False, hide_dollars=False, score_history=None):
    ticker = stock['Ticker']
    cols = st.columns([2, 4]) 
    is_search_or_watch = stock['Shares'] == 0 
    
    signal_tooltips = {
        "ADD 🟩": "Perfect confluence. Undervalued (low PEG/FCF) AND immediate technicals flashing buy (RSI, MACD, BB). Deploy capital now.",
        "HOLD/ACCUMULATE 🟨": "Macro trend and valuation healthy, but short-term headwinds. Buy in small, incremental tranches (DCA).",
        "HOLD ⬜": "Dead neutral zone. Metrics are actively fighting each other. Let the dust settle before deploying new money.",
        "TRIM 🟧": "Statistically exhausted/overbought. Rubber band stretched too tight. Sell 15-30% to lock in profits and reduce exposure.",
        "SELL 🟥": "Systemic breakdown across all horizons. Overvalued and big money is bailing. Liquidate, cut losses, and reallocate."
    }
    
    with cols[0]:
        title_col, btn_col = st.columns([3, 1])
        with title_col: 
            st.markdown(f"### **{ticker}**")
            st.markdown(f"<a href='https://finviz.com/quote.ashx?t={ticker}' target='_blank' style='text-decoration: none; font-size: 14px;'>🔗 Finviz</a>", unsafe_allow_html=True)
            
        with btn_col:
            if is_watchlist:
                if st.button("❌", key=f"remove_{ticker}"):
                    st.session_state.watchlist.remove(ticker)
                    st.rerun()

        trend_html = ""
        if score_history is not None and not score_history.empty:
            t_scores = score_history[score_history['Ticker'] == ticker].copy()
            if not t_scores.empty:
                ninety_days_ago = today_date - timedelta(days=90)
                t_scores_quarter = t_scores[t_scores['Date'] >= ninety_days_ago].sort_values('Date')
                
                if not t_scores_quarter.empty:
                    oldest_score = int(t_scores_quarter.iloc[0]['Score'])
                    current_score = int(stock['Score'])
                    diff = current_score - oldest_score
                    
                    if diff > 0:
                        trend_html = f" <span style='color:#2ca02c; font-size:13px;' title='Up {diff} pts over the last 90 days'><b>⬆️ (+{diff})</b></span>"
                    elif diff < 0:
                        trend_html = f" <span style='color:#d62728; font-size:13px;' title='Down {abs(diff)} pts over the last 90 days'><b>⬇️ ({diff})</b></span>"
                    elif len(t_scores_quarter) > 1:
                        trend_html = f" <span style='color:gray; font-size:13px;' title='Score unchanged over the last 90 days'><b>➖</b></span>"

        hover_text = signal_tooltips.get(stock['Decision'], "Quant Engine Signal")

        st.markdown(
            f"<div title='{hover_text}' style='border:1px solid {stock['D_Color']}; padding: 10px; border-radius: 5px; margin-bottom: 5px; cursor: help;'>"
            f"<h4 style='margin:0; color:{stock['D_Color']};'>Signal: {stock['Decision']}</h4>"
            f"<p style='margin:0; font-size:14px;'>Quant Score: <b>{stock['Score']}/100</b>{trend_html} | Risk: <span style='color:{stock['R_Color']};'><b>{stock['Risk']}</b></span></p>"
            f"</div>", unsafe_allow_html=True
        )
        
        with st.popover("📊 Score Breakdown", use_container_width=True):
            st.markdown(f"### {ticker} Algorithmic Breakdown")
            for line in stock.get('Breakdown', []):
                st.write(line)
        
        sub1, sub2 = st.columns(2)
        with sub1:
            st.write(f"**Price:** ${stock.get('Price', 0.0):.2f}")
            
            t_pe = stock.get('T_PE', 'N/A')
            f_pe = stock.get('F_PE', 'N/A')
            peg = stock.get('PEG', 'N/A')
            upside = stock.get('Upside', 'N/A')
            
            tpe_str = f"{t_pe:.1f}" if isinstance(t_pe, (float, int)) else 'N/A'
            fpe_str = f"{f_pe:.1f}" if isinstance(f_pe, (float, int)) else 'N/A'
            peg_str = f"{peg:.2f}" if isinstance(peg, (float, int)) else 'N/A'
            up_str = f"{upside:+.1f}%" if isinstance(upside, (float, int)) else 'N/A'
            
            st.write(f"**P/E (T|F):** {tpe_str} | {fpe_str}")
            st.write(f"**PEG:** {peg_str}")
            st.write(f"**Target Upside:** {up_str}")
            
        with sub2:
            st.write(f"**RSI:** {stock['RSI']}")
            macd_status = "Bullish" if (isinstance(stock['MACD'], (float, int)) and stock['MACD'] > stock['MACD_Sig']) else "Bearish"
            st.write(f"**MACD:** {macd_status}")

            ins_val = stock.get('Insiders', 'N/A')
            ins_str = f"{ins_val * 100:.1f}%" if isinstance(ins_val, (float, int)) else 'N/A'

            st.write(f"**P/C Ratio:** {stock['PC_Ratio']} | **Insiders:** {ins_str}")

        if not is_search_or_watch:
            ret = ((stock['Price'] - stock['Avg']) / stock['Avg']) * 100 if stock['Avg'] > 0 else 0
            
            avg_str = "$••••" if hide_dollars else f"${stock['Avg']:.2f}"
            val_str = "$••••" if hide_dollars else f"${stock['Val']:,.0f}"
            
            ret_color = "#2ca02c" if ret >= 0 else "#d62728"
            
            html_string = (
                f"<div style='font-size: 15px; margin-top: 5px; margin-bottom: 5px;'>"
                f"<b>My Return:</b> <span style='color:{ret_color}; font-weight:bold;'>{ret:+.2f}%</span> &nbsp;|&nbsp; "
                f"<b>Avg Cost:</b> {avg_str} &nbsp;|&nbsp; "
                f"<b>Value:</b> {val_str}"
                f"</div>"
            )
            st.markdown(html_string, unsafe_allow_html=True)
            
    master_hist = histories.get(ticker)
    if master_hist is not None and not master_hist.empty:
        if score_history is not None and not score_history.empty:
            t_scores = score_history[score_history['Ticker'] == ticker].copy()
            if not t_scores.empty:
                t_scores.set_index('Date', inplace=True)
                t_scores = t_scores[~t_scores.index.duplicated(keep='last')]
                master_hist = master_hist.join(t_scores['Score'], how='left')
                master_hist['Score'] = master_hist['Score'].ffill()
                
        if len(master_hist) > 20:
            master_hist['BB_Upper'], master_hist['BB_Lower'] = calculate_bbands(master_hist['Close'])

        with cols[1]:
            fig = go.Figure()
            
            if 'Score' in master_hist.columns and not master_hist['Score'].dropna().empty:
                fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['Score'], mode='lines', name='Quant Score', line=dict(color='rgba(255, 0, 255, 0.4)', width=2, dash='dot'), yaxis='y2'))

            if 'BB_Upper' in master_hist.columns and not master_hist['BB_Upper'].dropna().empty:
                fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['BB_Upper'], mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'))
                fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['BB_Lower'], mode='lines', line=dict(width=0), fill='tonexty', fillcolor='rgba(173, 216, 230, 0.1)', showlegend=False, hoverinfo='skip'))

            fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['Close'], mode='lines', name='Price', line=dict(color='#2ca02c', width=2.5)))
            
            if '200_WMA' in master_hist.columns and not master_hist['200_WMA'].dropna().empty:
                fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['200_WMA'], mode='lines', name='200 WMA', line=dict(color='darkorange', width=2, dash='dash')))
            if '50_SMA' in master_hist.columns and not master_hist['50_SMA'].dropna().empty:
                fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['50_SMA'], mode='lines', name='50 SMA', line=dict(color='gold', width=1.5, dash='dot')))
            if '200_SMA' in master_hist.columns and not master_hist['200_SMA'].dropna().empty:
                fig.add_trace(go.Scatter(x=master_hist.index, y=master_hist['200_SMA'], mode='lines', name='200 SMA', line=dict(color='mediumpurple', width=2, dash='dash')))
            
            if stock['Avg'] > 0:
                fig.add_hline(y=stock['Avg'], line_dash="dot", line_color="deepskyblue", line_width=2, opacity=0.8)
            
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0),
                xaxis=dict(
                    rangeselector=dict(
                        buttons=list([
                            dict(count=1, label="1m", step="month", stepmode="backward"),
                            dict(count=3, label="3m", step="month", stepmode="backward"),
                            dict(count=6, label="6m", step="month", stepmode="backward"),
                            dict(count=1, label="1y", step="year", stepmode="backward"),
                            dict(step="all", label="Max")
                        ]),
                        bgcolor='rgba(150, 150, 150, 0.1)',
                        activecolor='rgba(44, 160, 44, 0.5)'
                    ),
                    type="date"
                ),
                yaxis=dict(visible=True, side='left'), 
                yaxis2=dict(range=[0, 100], overlaying='y', side='right', visible=False), 
                showlegend=False, 
                height=350, 
                plot_bgcolor='rgba(0,0,0,0)', 
                hovermode='x unified'
            )
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            
    st.divider()

# --- APP LAYOUT ---
st.title("📈 Nightshift Quant Command Center")
today = pd.Timestamp.today().tz_localize(None)

global_scores_df = load_score_history()

# Setup Tabs for cleaner UI
tab_main, tab_analytics = st.tabs(["💻 Command Center", "📊 Performance Analytics"])

# -------------------------------
# TAB 1: COMMAND CENTER
# -------------------------------
with tab_main:
    st.markdown("### 🔍 Stock Research Station")
    search_query = st.text_input("Enter Ticker Symbol (e.g. MSFT, BMNR, QS):", "").strip().upper()

    if search_query:
        with st.spinner(f"Running algorithmic analysis on {search_query}..."):
            search_data, search_hist, _ = get_portfolio_data({search_query: {'shares': 0, 'avg_price': 0, 'pct_acct': 0, 'gl_pct': 0}})
            if search_data and search_data[0]['Price'] > 0:
                col1, col2 = st.columns([8, 1])
                with col2:
                    if search_query not in st.session_state.watchlist:
                        if st.button("⭐ Watch", key="add_watch"):
                            st.session_state.watchlist.append(search_query)
                            st.rerun()
                    else: st.button("✅ Added", disabled=True)
                draw_stock_row(search_data[0], search_hist, today, hide_dollars=hide_dollars, score_history=global_scores_df)
            else:
                st.warning(f"Could not find valid market data for '{search_query}'.")
    st.divider()

    if st.session_state.watchlist:
        st.markdown("### ⭐ My Watchlist")
        watch_dict = {ticker: {} for ticker in st.session_state.watchlist}
        with st.spinner("Updating Watchlist algorithms..."):
            watch_data, watch_hist, _ = get_portfolio_data(watch_dict)
        for stock in watch_data: draw_stock_row(stock, watch_hist, today, is_watchlist=True, hide_dollars=hide_dollars, score_history=global_scores_df)

    st.markdown("### 🏆 Top & Bottom Market Scanner")
    st.markdown("Live scan of a curated universe of global megacap and hyper-growth stocks to find the best (and worst) immediate setups.")

    global_universe = [
        'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSM', 'AVGO', 'NVO', 'JPM', 
        'WMT', 'LLY', 'V', 'PG', 'MA', 'JNJ', 'ASML', 'HD', 'ORCL', 'COST', 
        'CVX', 'BABA', 'CRM', 'AMD', 'BAC', 'PEP', 'LIN', 'KO', 'ADBE', 'DIS', 
        'CSCO', 'TM', 'INTC', 'VZ', 'PFE', 'NKE', 'SHEL', 'AZN', 'NVS', 'SAP', 
        'SNY', 'SONY', 'RY', 'PLTR', 'UBER', 'CRWD', 'PANW', 'ARM', 'SMCI', 'ALB', 'NFLX', 'CVS', 'HOOD', 'IBM', 'IREN', 'LRCX', 'AMAT', 'XOM', 'LMT', 'ZETA', 'ACHR', 'UNH', 'NKE', 'COIN', 'NVO', 'ANET', 'CRSP', 'CRWV', 'DIS', 'DUK', 'GLXY', 'LULU', 'NBIS', 'NRG', 'SBUX', 'TSLA'
    ]

    if st.checkbox("Run Market Scan (Takes ~20 seconds to load)"):
        scan_dict = {ticker: {} for ticker in global_universe}
        with st.spinner("Scanning global assets... (Cached after first run)"):
            market_data, market_hist, _ = get_portfolio_data(scan_dict)
            
        if market_data:
            df_market = pd.DataFrame(market_data)
            df_market['Upside_Safe'] = pd.to_numeric(df_market['Upside'], errors='coerce').fillna(0)
            
            # Sort by top score and upside
            df_sorted = df_market.sort_values(by=['Score', 'Upside_Safe'], ascending=[False, False])
            
            # Extract Top 5 and Bottom 5
            df_top5 = df_sorted.head(5)
            df_bottom5 = df_sorted.tail(5)
            df_combined = pd.concat([df_top5, df_bottom5]).drop_duplicates(subset=['Ticker'])
            
            export_cols = ['Ticker', 'Price', 'Score', 'Decision', 'Risk', 'Sector', 'ROE', 'Margins', 'PEG', 'Insiders', 'Upside', 'FCF_Y', 'RSI', 'PC_Ratio']
            df_export = df_combined[export_cols].copy()
            
            csv_data = df_export.to_csv(index=False).encode('utf-8')
            
            col_space, col_btn = st.columns([8, 2])
            with col_btn:
                st.download_button(
                    label="💾 Export Scanned Setups",
                    data=csv_data,
                    file_name=f"Quant_Scan_{today.strftime('%Y-%m-%d')}.csv",
                    mime="text/csv"
                )
            
            st.markdown("#### 🔥 Top 5 Best Setups")
            for idx, row in df_top5.iterrows():
                draw_stock_row(row.to_dict(), market_hist, today, hide_dollars=hide_dollars, score_history=global_scores_df)
                
            st.markdown("#### 🧊 Bottom 5 Worst Setups")
            for idx, row in df_bottom5.iterrows():
                draw_stock_row(row.to_dict(), market_hist, today, hide_dollars=hide_dollars, score_history=global_scores_df)
    st.divider()

    if portfolio:
        with st.spinner("Crunching data from the market..."):
            data, histories, total_val = get_portfolio_data(portfolio)

        if data:
            total_val_str = "$••••" if hide_dollars else f"${total_val:,.2f}"
            
            col_title, col_export = st.columns([8, 2])
            with col_title:
                st.subheader(f"Total Live Portfolio Value: {total_val_str}")
                
            with col_export:
                df_port = pd.DataFrame(data)
                port_cols = ['Ticker', 'Shares', 'Avg', 'Price', 'Score', 'Decision', 'Risk', 'Sector', 'ROE', 'PEG', 'Insiders', 'Upside']
                csv_port = df_port[port_cols].to_csv(index=False).encode('utf-8')
                st.download_button("💾 Export Portfolio Grades", data=csv_port, file_name=f"My_Portfolio_Grades_{today.strftime('%Y-%m-%d')}.csv", mime="text/csv")
                
            st.markdown("### 🩺 Portfolio Health & Diversification")
            
            df_metrics = pd.DataFrame(data)
            df_metrics['Weight'] = 0.0 
            
            if total_val > 0:
                df_metrics['Weight'] = df_metrics['Val'] / total_val
                weighted_score = (df_metrics['Score'] * df_metrics['Weight']).sum()
                avg_risk = (df_metrics['Risk_Pts'] * df_metrics['Weight']).sum()
            else:
                weighted_score, avg_risk = 50, 0
                
            health_color = "normal" if weighted_score >= 50 else "inverse"
            overall_health = "Excellent" if weighted_score >= 65 else "Good" if weighted_score >= 50 else "Warning"
            
            sector_weights = df_metrics.groupby('Sector')['Weight'].sum() * 100
            
            df_metrics['Is_Domestic'] = df_metrics['Country'] == 'United States'
            dom_weight = df_metrics[df_metrics['Is_Domestic']]['Weight'].sum() * 100
            intl_weight = df_metrics[~df_metrics['Is_Domestic']]['Weight'].sum() * 100
            
            h_cols = st.columns([0.8, .8, 1.3, 1.3])
            
            with h_cols[0]:
                st.metric(label="Overall Health Status", value=overall_health, delta=f"Quant Score: {weighted_score:.1f}/100", delta_color=health_color)
                r_str = "High ⚠️" if avg_risk >= 2 else "Low 🛡️" if avg_risk <= 0 else "Moderate ⚖️"
                st.metric(label="Aggregated Portfolio Risk", value=r_str)
                
            with h_cols[1]:
                st.markdown("**Suggestions & Warnings:**")
                suggestions = []
                
                max_pos = df_metrics.loc[df_metrics['Weight'].idxmax()]
                if max_pos['Weight'] > 0.20:
                    suggestions.append(f"⚠️ **Concentration:** {max_pos['Ticker']} makes up {max_pos['Weight']*100:.1f}% of your portfolio.")
                
                if not sector_weights.empty:
                    max_sector = sector_weights.idxmax()
                    if sector_weights[max_sector] > 40:
                        suggestions.append(f"⚠️ **Sector Risk:** You are heavily overweight in **{max_sector}** ({sector_weights[max_sector]:.1f}%).")
                        
                if intl_weight < 10:
                    suggestions.append(f"🌍 **Geo Risk:** Severe home-country bias. You have only {intl_weight:.1f}% international exposure.")
                elif intl_weight > 50:
                    suggestions.append(f"🌍 **Geo Risk:** High international exposure ({intl_weight:.1f}%) introduces significant FX currency risk.")
                        
                sell_candidates = df_metrics[df_metrics['Score'] < 40]
                if not sell_candidates.empty:
                    tickers_str = ", ".join(sell_candidates['Ticker'].tolist())
                    suggestions.append(f"💡 **Action Needed:** Algorithm flagged {tickers_str} as TRIM or SELL.")
                    
                if not suggestions: st.success("✅ Well-diversified and balanced. No structural warnings.")
                else:
                    for sug in suggestions: st.info(sug)
                    
            with h_cols[2]:
                fig_sector = go.Figure(data=[go.Pie(labels=sector_weights.index, values=sector_weights.values, hole=.5, textinfo='label+percent')])
                fig_sector.update_layout(title_text="Sector Diversification", margin=dict(t=30, b=0, l=0, r=0), height=250)
                st.plotly_chart(fig_sector, use_container_width=True)

            with h_cols[3]:
                geo_labels = ['Domestic (US)', 'International']
                geo_vals = [dom_weight, intl_weight]
                fig_geo = go.Figure(data=[go.Pie(labels=geo_labels, values=geo_vals, hole=.5, textinfo='label+percent', marker=dict(colors=['#1f77b4', '#ff7f0e']))])
                fig_geo.update_layout(title_text="Geographic Exposure", margin=dict(t=30, b=0, l=0, r=0), height=250)
                st.plotly_chart(fig_geo, use_container_width=True)

            st.divider()
            st.markdown("### 🎯 Algorithmic Asset Analysis")
            for stock in data: draw_stock_row(stock, histories, today, hide_dollars=hide_dollars, score_history=global_scores_df)


# -------------------------------
# TAB 2: PERFORMANCE ANALYTICS
# -------------------------------
with tab_analytics:
    st.markdown("### 📈 Forward Strategy Performance")
    st.markdown("Tracking normalized percentage gain/loss of tickers starting the **day after** they trigger a primary signal (Buy ≥ 85, Sell < 40).")
    
    if global_scores_df is not None and not global_scores_df.empty:
        df_hist = global_scores_df.copy()
        
        # Ensure correct datatypes
        df_hist['Date'] = pd.to_datetime(df_hist['Date'])
        df_hist['Price'] = pd.to_numeric(df_hist['Price'], errors='coerce')
        df_hist['Score'] = pd.to_numeric(df_hist['Score'], errors='coerce')
        df_hist = df_hist.dropna(subset=['Date', 'Price', 'Score']).sort_values('Date')
        
        # ----------------------------------------
        # CHART 1: 85+ RATING (BUY) FORWARD RETURN
        # ----------------------------------------
        buy_combined = []
        for ticker, group in df_hist.groupby('Ticker'):
            group = group.sort_values('Date').reset_index(drop=True)
            idx_85 = group.index[group['Score'] >= 85].tolist()
            
            if not idx_85: continue
            
            first_85 = idx_85[0]
            # Must have data the day *after* to start tracking
            if first_85 + 1 < len(group):
                start_idx = first_85 + 1
                start_price = group.loc[start_idx, 'Price']
                
                if start_price > 0:
                    track_df = group.loc[start_idx:].copy()
                    track_df['Pct_Gain'] = ((track_df['Price'] - start_price) / start_price) * 100
                    buy_combined.append(track_df)
        
        if buy_combined:
            buy_df_all = pd.concat(buy_combined)
            fig_buy = go.Figure()
            
            # Add trace for each ticker
            for ticker, grp in buy_df_all.groupby('Ticker'):
                # Green marker if score maintained >= 85, gray if it dropped
                colors = ['#2ca02c' if s >= 85 else 'gray' for s in grp['Score']]
                fig_buy.add_trace(go.Scatter(
                    x=grp['Date'], y=grp['Pct_Gain'], 
                    mode='lines+markers', 
                    name=ticker,
                    line=dict(width=1, color='rgba(150,150,150,0.4)'),
                    marker=dict(color=colors, size=6),
                    text=[f"<b>{ticker}</b><br>Score: {s}<br>Gain: {g:.2f}%" for s, g in zip(grp['Score'], grp['Pct_Gain'])],
                    hoverinfo='text'
                ))
            
            # Add Average Line across the cohort
            avg_buy = buy_df_all.groupby('Date')['Pct_Gain'].mean().reset_index()
            fig_buy.add_trace(go.Scatter(
                x=avg_buy['Date'], y=avg_buy['Pct_Gain'],
                mode='lines', name='Cohort Average',
                line=dict(color='yellow', width=3, dash='dash')
            ))
            
            fig_buy.update_layout(
                title="85+ Score (Buy) - Percentage Gain Over Time",
                yaxis_title="Percentage Gain/Loss (%)",
                xaxis_title="Date",
                hovermode='x unified',
                showlegend=False,
                height=500
            )
            st.plotly_chart(fig_buy, use_container_width=True)
        else:
            st.info("No tickers found with an 85+ score and subsequent price data yet.")
            
        st.divider()

        # ----------------------------------------
        # CHART 2: <40 RATING (SELL) FORWARD RETURN
        # ----------------------------------------
        sell_combined = []
        for ticker, group in df_hist.groupby('Ticker'):
            group = group.sort_values('Date').reset_index(drop=True)
            idx_sell = group.index[group['Score'] < 40].tolist()
            
            if not idx_sell: continue
            
            first_sell = idx_sell[0]
            if first_sell + 1 < len(group):
                start_idx = first_sell + 1
                start_price = group.loc[start_idx, 'Price']
                
                if start_price > 0:
                    track_df = group.loc[start_idx:].copy()
                    track_df['Pct_Gain'] = ((track_df['Price'] - start_price) / start_price) * 100
                    sell_combined.append(track_df)
        
        if sell_combined:
            sell_df_all = pd.concat(sell_combined)
            fig_sell = go.Figure()
            
            for ticker, grp in sell_df_all.groupby('Ticker'):
                # Red marker if score maintained < 40, gray if it recovered
                colors = ['#d62728' if s < 40 else 'gray' for s in grp['Score']]
                fig_sell.add_trace(go.Scatter(
                    x=grp['Date'], y=grp['Pct_Gain'], 
                    mode='lines+markers', 
                    name=ticker,
                    line=dict(width=1, color='rgba(150,150,150,0.4)'),
                    marker=dict(color=colors, size=6),
                    text=[f"<b>{ticker}</b><br>Score: {s}<br>Gain: {g:.2f}%" for s, g in zip(grp['Score'], grp['Pct_Gain'])],
                    hoverinfo='text'
                ))
            
            avg_sell = sell_df_all.groupby('Date')['Pct_Gain'].mean().reset_index()
            fig_sell.add_trace(go.Scatter(
                x=avg_sell['Date'], y=avg_sell['Pct_Gain'],
                mode='lines', name='Cohort Average',
                line=dict(color='yellow', width=3, dash='dash')
            ))
            
            fig_sell.update_layout(
                title="<40 Score (Sell) - Percentage Gain Over Time",
                yaxis_title="Percentage Gain/Loss (%)",
                xaxis_title="Date",
                hovermode='x unified',
                showlegend=False,
                height=500
            )
            st.plotly_chart(fig_sell, use_container_width=True)
        else:
            st.info("No tickers found with a <40 score and subsequent price data yet.")

    else:
        st.warning("Insufficient historical data found to generate performance charts.")