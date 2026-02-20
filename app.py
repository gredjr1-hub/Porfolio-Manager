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

# --- SESSION STATE FOR AUDIO ---
if 'startup_sound_played' not in st.session_state:
    st.session_state.startup_sound_played = False

st.set_page_config(page_title="Quant Command Center", layout="wide", page_icon="📈")

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
        # --- BYPASS FIDELITY FOOTER BUG ---
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
                
                # --- LOT AGGREGATION LOGIC ---
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
                
                # --- LOT AGGREGATION LOGIC ---
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
timeframes = {'1M': 30, '3M': 90, '6M': 180, '1Y': 365, '5Y': 1825}

@st.cache_data(ttl=3600) 
def get_portfolio_data(port_dict):
    if not port_dict: return [], {}, 0 
    
    portfolio_data = []
    all_histories = {} 
    total_value = 0

    for ticker, data in port_dict.items():
        shares, avg_price = data.get('shares', 0), data.get('avg_price', 0)
        stock = yf.Ticker(ticker)
        
        # --- 1. GET PRICE SAFELY FIRST ---
        hist = stock.history(period='max')
        if not hist.empty:
            current_price = hist['Close'].iloc[-1]
            hist.index = hist.index.tz_localize(None)
            all_histories[ticker] = hist
        else:
            current_price = 0.0
            
        # --- 2. GET FUNDAMENTALS SEPARATELY ---
        try:
            info = stock.info
            t_pe = info.get('trailingPE', 'N/A')
            f_pe = info.get('forwardPE', 'N/A')
            peg = info.get('trailingPegRatio', info.get('pegRatio', 'N/A'))
            insiders = info.get('heldPercentInsiders', 'N/A')
           
            fcf = info.get('freeCashflow', 'N/A')
            mkt_cap = info.get('marketCap', 'N/A')
            target = info.get('targetMeanPrice', 'N/A')
            sector = info.get('sector', 'Unknown')
            country = info.get('country', 'Unknown')
            
            fcf_yield = (fcf / mkt_cap * 100) if isinstance(fcf, (int, float)) and isinstance(mkt_cap, (int, float)) and mkt_cap > 0 else 'N/A'
            upside = ((target - current_price) / current_price * 100) if isinstance(target, (int, float)) and target > 0 and current_price > 0 else 'N/A'
        except:
            t_pe, f_pe, peg, insiders, fcf_yield, target, upside, sector, country = 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'N/A', 'Unknown', 'Unknown'
            
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
        
        if isinstance(peg, (float, int)):
            if peg < 1.0: 
                score += 15
                breakdown.append("✅ **PEG < 1.0:** +15 pts (Undervalued growth)")
            elif peg > 2.5: 
                score -= 15; risk_points += 1
                breakdown.append("❌ **PEG > 2.5:** -15 pts (Overvalued) [+1 Risk]")
            
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
            if fcf_yield > 5.0: 
                score += 10
                breakdown.append("✅ **FCF Yield > 5%:** +10 pts (Strong cash generation)")
            elif fcf_yield < 0: 
                score -= 10; risk_points += 1
                breakdown.append("❌ **Negative FCF Yield:** -10 pts (Cash burn) [+1 Risk]")
            
        if isinstance(upside, (float, int)):
            if upside > 15: 
                score += 10
                breakdown.append(f"✅ **Analyst Upside > 15%:** +10 pts")
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
            if rsi_14 < 35: 
                score += 10
                breakdown.append("✅ **RSI < 35:** +10 pts (Oversold/Value Zone)")
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
                score += 10
                breakdown.append("✅ **Price below Lower BB:** +10 pts (Mean reversion bounce)")
            elif current_price > bb_upper: 
                score -= 10
                breakdown.append("❌ **Price above Upper BB:** -10 pts (Overextended)")
                
        if vol_surge and (hist['Close'].iloc[-1] > hist['Open'].iloc[-1]): 
            score += 5 
            breakdown.append("✅ **Bullish Volume Surge:** +5 pts (Institutional buying)")
            
        if isinstance(pc_ratio, (float, int)):
            if pc_ratio < 0.7: 
                score += 5
                breakdown.append("✅ **Put/Call < 0.7:** +5 pts (Bullish options flow)")
            elif pc_ratio > 1.2: 
                score -= 5
                breakdown.append("❌ **Put/Call > 1.2:** -5 pts (Bearish options flow)")
            
        if volatility > 60: 
            risk_points += 2
            breakdown.append("⚠️ **High Volatility (>60%):** [+2 Risk]")
        elif volatility < 20: 
            risk_points -= 1
            breakdown.append("🛡️ **Low Volatility (<20%):** [-1 Risk]")

        score = max(0, min(100, int(score))) 
        breakdown.append(f"---\n🎯 **Final Quant Score: {score}/100**")
        
        if score >= 80: decision, d_color = "ADD 🟩", "#28a745"
        elif score >= 60: decision, d_color = "HOLD/ACCUMULATE 🟨", "#17a2b8"
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
            'Sector': sector, 'Country': country, 'T_PE': t_pe, 'F_PE': f_pe, 'PEG': peg, 'Insiders': insiders, 
            'Target': target, 'Upside': upside, 'FCF_Y': fcf_yield,
            'RSI': rsi_14, 'MACD': macd_val, 'MACD_Sig': sig_val, 'PC_Ratio': pc_ratio, 'Vol': volatility,
            'Score': score, 'Decision': decision, 'D_Color': d_color, 'Risk': risk_lvl, 'R_Color': r_color, 'Risk_Pts': risk_points,
            'Upper_BB': bb_upper, 'Lower_BB': bb_lower,
            'pct_acct': data.get('pct_acct', 0.0), 'gl_pct': data.get('gl_pct', 0.0),
            'Breakdown': breakdown
        })

    return portfolio_data, all_histories, total_value

# --- UI HELPER FUNCTION ---
def draw_stock_row(stock, histories, today_date, is_watchlist=False, hide_dollars=False):
    ticker = stock['Ticker']
    cols = st.columns([1.6, 1, 1, 1, 1, 1]) 
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
        with title_col: st.markdown(f"### **{ticker}**")
        with btn_col:
            if is_watchlist:
                if st.button("❌", key=f"remove_{ticker}"):
                    st.session_state.watchlist.remove(ticker)
                    st.rerun()

        hover_text = signal_tooltips.get(stock['Decision'], "Quant Engine Signal")

        st.markdown(
            f"<div title='{hover_text}' style='border:1px solid {stock['D_Color']}; padding: 10px; border-radius: 5px; margin-bottom: 5px; cursor: help;'>"
            f"<h4 style='margin:0; color:{stock['D_Color']};'>Signal: {stock['Decision']}</h4>"
            f"<p style='margin:0; font-size:14px;'>Quant Score: <b>{stock['Score']}/100</b> | Risk: <span style='color:{stock['R_Color']};'><b>{stock['Risk']}</b></span></p>"
            f"</div>", unsafe_allow_html=True
        )
        
        # --- NEW: SCORE BREAKDOWN POPOVER ---
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
            
            # Use dot characters so Markdown doesn't get confused by asterisks
            avg_str = "$••••" if hide_dollars else f"${stock['Avg']:.2f}"
            val_str = "$••••" if hide_dollars else f"${stock['Val']:,.0f}"
            
            # Use native Streamlit colors
            ret_color = "green" if ret >= 0 else "red"
            st.markdown(f"**My Return:** :{ret_color}[{ret:+.2f}%] | **Avg Cost:** {avg_str} | **Value:** {val_str}")
            
    master_hist = histories.get(ticker)
    if master_hist is not None and not master_hist.empty:
        if len(master_hist) > 20:
            master_hist['BB_Upper'], master_hist['BB_Lower'] = calculate_bbands(master_hist['Close'])

        for i, (tf_label, days_back) in enumerate(timeframes.items()):
            with cols[i+1]:
                start_date = today_date - timedelta(days=days_back)
                sliced_hist = master_hist[master_hist.index >= start_date]
                if not sliced_hist.empty:
                    start_p = sliced_hist['Close'].iloc[0]
                    end_p = sliced_hist['Close'].iloc[-1]
                    line_color = '#2ca02c' if end_p >= start_p else '#d62728'
                    
                    tf_ret = ((end_p - start_p) / start_p) * 100 if start_p > 0 else 0
                    header_text = f"{tf_label} <span style='color:{line_color}; font-size:13px;'>({tf_ret:+.2f}%)</span>"
                    
                    fig = go.Figure()
                    if 'BB_Upper' in sliced_hist.columns and not sliced_hist['BB_Upper'].dropna().empty:
                        fig.add_trace(go.Scatter(x=sliced_hist.index, y=sliced_hist['BB_Upper'], mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'))
                        fig.add_trace(go.Scatter(x=sliced_hist.index, y=sliced_hist['BB_Lower'], mode='lines', line=dict(width=0), fill='tonexty', fillcolor='rgba(173, 216, 230, 0.2)', showlegend=False, hoverinfo='skip'))

                    fig.add_trace(go.Scatter(x=sliced_hist.index, y=sliced_hist['Close'], mode='lines', name='Price', line=dict(color=line_color, width=2.5)))
                    
                    if '200_WMA' in sliced_hist.columns and not sliced_hist['200_WMA'].dropna().empty:
                        fig.add_trace(go.Scatter(x=sliced_hist.index, y=sliced_hist['200_WMA'], mode='lines', name='200 WMA', line=dict(color='darkorange', width=2, dash='dash')))
                        
                    if '50_SMA' in sliced_hist.columns and not sliced_hist['50_SMA'].dropna().empty:
                        fig.add_trace(go.Scatter(x=sliced_hist.index, y=sliced_hist['50_SMA'], mode='lines', name='50 SMA', line=dict(color='gold', width=1.5, dash='dot')))
                        
                    if '200_SMA' in sliced_hist.columns and not sliced_hist['200_SMA'].dropna().empty:
                        fig.add_trace(go.Scatter(x=sliced_hist.index, y=sliced_hist['200_SMA'], mode='lines', name='200 SMA', line=dict(color='mediumpurple', width=2, dash='dash')))
                    
                    if stock['Avg'] > 0:
                        fig.add_hline(y=stock['Avg'], line_dash="dot", line_color="deepskyblue", line_width=2, opacity=0.8)
                    
                    fig.update_layout(
                        title=dict(text=header_text, font=dict(size=14)), margin=dict(l=0, r=0, t=30, b=0),
                        xaxis=dict(visible=False), yaxis=dict(visible=False), showlegend=False, height=190,
                        plot_bgcolor='rgba(0,0,0,0)', hovermode='x unified'
                    )
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
    st.divider()

# --- APP LAYOUT ---
st.title("📈 Nightshift Quant Command Center")
today = pd.Timestamp.today().tz_localize(None)

# --- 1. RESEARCH STATION ---
st.markdown("### 🔍 Stock Research Station")
search_query = st.text_input("Enter Ticker Symbol (e.g. MSFT, BMNR, QS):", "").strip().upper()

if search_query:
    with st.spinner(f"Running 8-metric algorithmic analysis on {search_query}..."):
        search_data, search_hist, _ = get_portfolio_data({search_query: {'shares': 0, 'avg_price': 0, 'pct_acct': 0, 'gl_pct': 0}})
        if search_data and search_data[0]['Price'] > 0:
            col1, col2 = st.columns([8, 1])
            with col2:
                if search_query not in st.session_state.watchlist:
                    if st.button("⭐ Watch", key="add_watch"):
                        st.session_state.watchlist.append(search_query)
                        st.rerun()
                else: st.button("✅ Added", disabled=True)
            draw_stock_row(search_data[0], search_hist, today, hide_dollars=hide_dollars)
        else:
            st.warning(f"Could not find valid market data for '{search_query}'.")
st.divider()

# --- 2. WATCHLIST VIEW ---
if st.session_state.watchlist:
    st.markdown("### ⭐ My Watchlist")
    watch_dict = {ticker: {} for ticker in st.session_state.watchlist}
    with st.spinner("Updating Watchlist algorithms..."):
        watch_data, watch_hist, _ = get_portfolio_data(watch_dict)
    for stock in watch_data: draw_stock_row(stock, watch_hist, today, is_watchlist=True, hide_dollars=hide_dollars)

# --- 3. TOP 10 MARKET SCANNER ---
st.markdown("### 🏆 Top 10 Market Scanner")
st.markdown("Live scan of a curated universe of 50 global megacap and hyper-growth stocks to find the best immediate setups.")

global_universe = [
    'AAPL', 'MSFT', 'NVDA', 'GOOGL', 'AMZN', 'META', 'TSM', 'AVGO', 'NVO', 'JPM', 
    'WMT', 'LLY', 'V', 'PG', 'MA', 'JNJ', 'ASML', 'HD', 'ORCL', 'COST', 
    'CVX', 'BABA', 'CRM', 'AMD', 'BAC', 'PEP', 'LIN', 'KO', 'ADBE', 'DIS', 
    'CSCO', 'TM', 'INTC', 'VZ', 'PFE', 'NKE', 'SHEL', 'AZN', 'NVS', 'SAP', 
    'SNY', 'SONY', 'RY', 'PLTR', 'UBER', 'CRWD', 'PANW', 'ARM', 'SMCI', 'ALB', 'NFLX', 'CVS', 'HOOD'
]

if st.checkbox("Run Market Scan (Takes ~20 seconds to load)"):
    scan_dict = {ticker: {} for ticker in global_universe}
    with st.spinner("Scanning 50 global assets... (Cached after first run)"):
        market_data, market_hist, _ = get_portfolio_data(scan_dict)
        
    if market_data:
        df_market = pd.DataFrame(market_data)
        df_market['Upside_Safe'] = pd.to_numeric(df_market['Upside'], errors='coerce').fillna(0)
        df_top10 = df_market.sort_values(by=['Score', 'Upside_Safe'], ascending=[False, False]).head(10)
        
        # Select only the relevant columns for a clean spreadsheet
        export_cols = ['Ticker', 'Price', 'Score', 'Decision', 'Risk', 'Sector', 'T_PE', 'F_PE', 'PEG', 'Insiders', 'Upside', 'FCF_Y', 'RSI', 'PC_Ratio']
        df_export = df_top10[export_cols].copy()
        
        csv_data = df_export.to_csv(index=False).encode('utf-8')
        
        col_space, col_btn = st.columns([8, 2])
        with col_btn:
            st.download_button(
                label="💾 Export Top 10 to CSV",
                data=csv_data,
                file_name=f"Quant_Top10_Scan_{today.strftime('%Y-%m-%d')}.csv",
                mime="text/csv"
            )
        
        for idx, row in df_top10.iterrows():
            draw_stock_row(row.to_dict(), market_hist, today, hide_dollars=hide_dollars)
st.divider()

# --- 4. MACRO PORTFOLIO VIEW ---
if portfolio:
    with st.spinner("Crunching data from the market..."):
        data, histories, total_val = get_portfolio_data(portfolio)

    if data:
        # Mask Total Portfolio Value
        total_val_str = "$••••" if hide_dollars else f"${total_val:,.2f}"
        
        col_title, col_export = st.columns([8, 2])
        with col_title:
            st.subheader(f"Total Live Portfolio Value: {total_val_str}")
            
        with col_export:
            df_port = pd.DataFrame(data)
            port_cols = ['Ticker', 'Shares', 'Avg', 'Price', 'Score', 'Decision', 'Risk', 'Sector', 'PEG', 'Insiders', 'Upside']
            csv_port = df_port[port_cols].to_csv(index=False).encode('utf-8')
            st.download_button("💾 Export Portfolio Grades", data=csv_port, file_name=f"My_Portfolio_Grades_{today.strftime('%Y-%m-%d')}.csv", mime="text/csv")
            
        st.markdown("### 🩺 Portfolio Health & Diversification")
        
        df_metrics = pd.DataFrame(data)
        
        # --- THE FIX: Force the Weight column to exist no matter what! ---
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
        for stock in data: draw_stock_row(stock, histories, today, hide_dollars=hide_dollars)