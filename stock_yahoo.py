import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import pandas as pd
import threading
import twstock
import json
import os
import sys

# 嘗試匯入 yfinance
try:
    import yfinance as yf
except ImportError:
    print("錯誤: 找不到 yfinance，請執行: pip install yfinance")
    sys.exit(1)

# 嘗試匯入繪圖套件
try:
    import mplfinance as mpf
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    import matplotlib.pyplot as plt
    # 設定 matplotlib 支援中文 (Windows 適用)
    plt.rcParams['font.sans-serif'] = ['Microsoft JhengHei']
    plt.rcParams['axes.unicode_minus'] = False
except ImportError:
    print("錯誤: 找不到繪圖套件，請執行: pip install matplotlib mplfinance")
    sys.exit(1)


# ==========================================
# 共用工具函式
# ==========================================
def tf_text_to_code(tf_txt):
    if "日" in tf_txt: return "D"
    if "周" in tf_txt: return "W"
    return "M"

def fillna_ohlcv(df):
    df = df.dropna(subset=['Close'])
    for col in ['Open', 'High', 'Low']: 
        df[col] = df[col].fillna(df['Close'])
    df['Volume'] = df['Volume'].fillna(0)
    return df

def calc_kd(df, n=9):
    if len(df) < n:
        df['K'] = 50
        df['D'] = 50
        return df

    low_min = df['Low'].rolling(window=n).min()
    high_max = df['High'].rolling(window=n).max()
    denominator = high_max - low_min
    rsv = 100 * (df['Close'] - low_min) / denominator.replace(0, float('nan'))
    rsv = rsv.fillna(50)

    k_list, d_list = [], []
    k_curr, d_curr = 50, 50
    for r in rsv.values:
        k_curr = (2/3) * k_curr + (1/3) * r
        d_curr = (2/3) * d_curr + (1/3) * k_curr
        k_list.append(k_curr)
        d_list.append(d_curr)

    df['K'] = k_list
    df['D'] = d_list
    return df

def calc_macd(df):
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIF'] = exp12 - exp26
    df['DEM'] = df['DIF'].ewm(span=9, adjust=False).mean()
    df['OSC'] = df['DIF'] - df['DEM']
    return df


# ==========================================
# 1. 資料管理層
# ==========================================
class CategoryManager:
    def __init__(self):
        self.market_map = {'上市': {}, '上櫃': {}}
        for code, info in twstock.codes.items():
            if info.type == '股票':
                m, g = info.market, info.group
                if m in self.market_map:
                    if g not in self.market_map[m]: 
                        self.market_map[m][g] = []
                    self.market_map[m][g].append(code)

    def get_stocks(self, market_selection, industry):
        results = []
        target_markets = ['上市', '上櫃'] if market_selection == "全部 (上市+上櫃)" else [market_selection]
        for m in target_markets:
            if m not in self.market_map: continue
            
            target_inds = list(self.market_map[m].keys()) if industry == "全部產業" else ([industry] if industry in self.market_map[m] else [])
            for ind_name in target_inds:
                for code in self.market_map[m][ind_name]:
                    name = twstock.codes[code].name if code in twstock.codes else ""
                    results.append((code, name, m, ind_name))
        return sorted(list(set(results)), key=lambda x: x[0])

    def get_industries(self, market_selection):
        all_inds = set()
        target_markets = ['上市', '上櫃'] if market_selection == "全部 (上市+上櫃)" else [market_selection]
        for m in target_markets:
            if m in self.market_map: 
                all_inds.update(self.market_map[m].keys())
        return ["全部產業"] + sorted(list(all_inds))


# ==========================================
# 2. 核心分析層 (掃描用)
# ==========================================
class StockAnalyzer:
    MA_PERIODS = {'D': [5, 10, 20, 60, 120], 'W': [5, 10, 20, 40], 'M': [5, 10, 20, 40, 60]}

    def __init__(self):
        self.raw_df = None
        self.data_map = {}
        self.full_dfs = {}

    def fetch_data(self, stock_code, market_type):
        try:
            symbol = f"{stock_code}{'.TW' if market_type == '上市' else '.TWO'}"
            df = yf.Ticker(symbol).history(period="10y", auto_adjust=False)
            if df.empty: return False, "無資料"
            
            df = fillna_ohlcv(df.reset_index()[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']].copy())
            if df.empty: return False, "無有效資料"
            
            if 'Date' in df.columns: 
                df['Date'] = pd.to_datetime(df['Date']).dt.tz_localize(None)
            self.raw_df = df
            return True, "下載成功"
        except Exception as e:
            return False, str(e)

    def _build_tf_df(self, df_d, resample_rule=None):
        if resample_rule is None:
            df, tf = df_d.copy(), 'D'
        else:
            df = df_d.resample(resample_rule).agg({'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}).dropna(subset=['Close'])
            for col in ['Open', 'High', 'Low']: 
                df[col] = df[col].fillna(df['Close'])
            df['Volume'] = df['Volume'].fillna(0)
            tf = 'W' if resample_rule == 'W' else 'M'
            
        for p in self.MA_PERIODS[tf]: 
            df[f'MA{p}'] = df['Close'].rolling(p).mean()
        return calc_macd(calc_kd(df))

    def process_timeframes(self):
        if self.raw_df is None or len(self.raw_df) < 60: return False
        df_d = self.raw_df.copy().set_index('Date')
        
        self.full_dfs = {
            'D': self._build_tf_df(df_d), 
            'W': self._build_tf_df(df_d, 'W'), 
            'M': self._build_tf_df(df_d, 'ME')
        }
        self.data_map = {tf: df.iloc[-1] for tf, df in self.full_dfs.items() if not df.empty}
        return True

    def get_current_price(self):
        return self.data_map.get('D', {}).get('Close', self.data_map.get('W', {}).get('Close', 0))

    def check_volume(self, tf, n_periods, threshold, op):
        if tf not in self.full_dfs: return False
        vol_sum = self.full_dfs[tf].tail(n_periods)['Volume'].sum() / 1000.0
        return vol_sum >= threshold if op == '>=' else vol_sum <= threshold

    def check_ma_gap(self, tf, ma1, ma2, limit_pct):
        if tf not in self.data_map: return False
        val1, val2 = self.data_map[tf].get(ma1), self.data_map[tf].get(ma2)
        if pd.isna(val1) or pd.isna(val2): return False
        return abs(val1 - val2) / val2 * 100 <= limit_pct

    def check_kd_advanced(self, tf, k_op, k_val, gap_limit, k_gt_d=False, k_gt_d_n=3, tolerance=2.0):
        if tf not in self.full_dfs or len(self.full_dfs[tf]) < 1: return False, ""
        
        k, d = self.full_dfs[tf].iloc[-1].get('K'), self.full_dfs[tf].iloc[-1].get('D')
        if pd.isna(k) or pd.isna(d): return False, ""
        
        if (k_op == '>=' and k < k_val) or (k_op == '<=' and k > k_val) or abs(k - d) > gap_limit: 
            return False, ""
            
        if k_gt_d:
            if len(self.full_dfs[tf]) < k_gt_d_n: return False, "" 
            for _, r in self.full_dfs[tf].tail(k_gt_d_n).iterrows():
                if r['K'] < (r['D'] - tolerance): 
                    return False, ""
                    
        return True, f"K={k:.1f},D={d:.1f},Gap={abs(k-d):.1f}"

    def check_macd(self, tf, osc_only_red):
        osc = self.data_map.get(tf, {}).get('OSC')
        if pd.isna(osc) or (osc_only_red and osc <= 0): return False, ""
        return True, f"MACD(柱)={osc:.2f}"

    def check_price_bias_abs(self, tf, ma, limit_pct):
        close, ma_val = self.data_map.get(tf, {}).get('Close'), self.data_map.get(tf, {}).get(ma)
        if pd.isna(close) or pd.isna(ma_val) or ma_val == 0: return False, ""
        bias = (close - ma_val) / ma_val * 100
        return (True, f"Bias({tf}.{ma})={bias:.2f}%") if abs(bias) <= limit_pct else (False, "")

    def check_consecutive_trend(self, tf, n, ma, op):
        if tf not in self.full_dfs or len(self.full_dfs[tf]) < n: return False, ""
        for _, row in self.full_dfs[tf].tail(n).iterrows():
            close, ma_val = row.get('Close'), row.get(ma)
            if pd.isna(close) or pd.isna(ma_val) or (op == ">=" and close < ma_val) or (op == "<=" and close > ma_val): 
                return False, ""
        return True, f"連續{n}根{tf}收{op}{ma}"

    def check_retracement_close_only(self, tf, n, op, limit_pct):
        if tf not in self.full_dfs or len(self.full_dfs[tf]) < n: return False, ""
        subset = self.full_dfs[tf].tail(n)
        max_close = subset['Close'].max()
        if pd.isna(max_close) or max_close == 0: return False, ""
        
        retracement = (max_close - subset.loc[subset['Close'].idxmax():]['Close'].min()) / max_close * 100
        if pd.isna(retracement) or (op == ">=" and retracement < limit_pct) or (op == "<=" and retracement > limit_pct): 
            return False, ""
        return True, f"高點(收)後回檔={retracement:.1f}%"

    def check_flexible_breakout(self, check_5, check_10, check_20):
        if 'W' not in self.full_dfs or len(self.full_dfs['W']) < 2: return False, ""
        c_curr, c_prev = self.full_dfs['W'].iloc[-1]['Close'], self.full_dfs['W'].iloc[-2]['Close']
        conditions, broken_mas = [], []
        
        for enabled, ma_col, label in [(check_5, 'MA5', '5'), (check_10, 'MA10', '10'), (check_20, 'MA20', '20')]:
            if not enabled: continue
            ma_curr, ma_prev = self.full_dfs['W'].iloc[-1].get(ma_col), self.full_dfs['W'].iloc[-2].get(ma_col)
            if pd.notna(ma_curr) and pd.notna(ma_prev) and c_prev < ma_prev and c_curr > ma_curr:
                conditions.append(True)
                broken_mas.append(label)
            else: 
                conditions.append(False)
                
        return (True, f"周K站上MA({','.join(broken_mas)})") if conditions and all(conditions) else (False, "")

    def check_conditions(self, conditions):
        for cond in conditions:
            vl = self.data_map.get(cond['tf'], {}).get(cond['left'])
            vr = self.data_map.get(cond['tf'], {}).get(cond['right'])
            if pd.isna(vl) or pd.isna(vr) or (cond['op'] == '>=' and vl < vr) or (cond['op'] == '<=' and vl > vr): 
                return False
        return True


# ==========================================
# 3. 進階圖表視窗類別
# ==========================================
class ChartPopup:
    def __init__(self, parent, code, name, m_type):
        self.code, self.name, self.symbol = code, name, f"{code}{'.TW' if m_type == '上市' else '.TWO'}"
        self.interval_map = {"日線": "1d", "周線": "1wk", "月線": "1mo"}
        self.period_map_ui = {"1年": "1y", "2年": "2y", "3年": "3y", "5年": "5y", "10年": "10y", "max": "max"}

        self.top = tk.Toplevel(parent)
        self.top.title(f"{code} {name} - 技術分析圖")
        self.top.geometry("1200x800")

        f_ctrl = ttk.Frame(self.top, padding=5, relief="groove")
        f_ctrl.pack(side="top", fill="x")
        
        self.var_interval = tk.StringVar(value="日線")
        self.var_period = tk.StringVar(value="5年")
        self.var_vol = tk.BooleanVar(value=True)
        self.var_kd = tk.BooleanVar(value=True)
        self.var_macd = tk.BooleanVar(value=True)

        ttk.Label(f_ctrl, text="週期:").pack(side="left")
        ttk.Combobox(f_ctrl, textvariable=self.var_interval, values=list(self.interval_map.keys()), state="readonly", width=6).pack(side="left", padx=5)
        
        ttk.Label(f_ctrl, text="範圍:").pack(side="left")
        ttk.Combobox(f_ctrl, textvariable=self.var_period, values=list(self.period_map_ui.keys()), state="readonly", width=6).pack(side="left", padx=5)
        ttk.Separator(f_ctrl, orient="vertical").pack(side="left", fill="y", padx=10)
        
        for text, var in [("成交量", self.var_vol), ("KD", self.var_kd), ("MACD", self.var_macd)]:
            ttk.Checkbutton(f_ctrl, text=text, variable=var, command=self.draw).pack(side="left", padx=5)
            
        ttk.Button(f_ctrl, text="重整", command=self.draw).pack(side="left", padx=20)

        self.canvas_frame = tk.Frame(self.top)
        self.canvas_frame.pack(side="bottom", fill="both", expand=True)
        self.canvas = None
        self.toolbar = None
        self.draw()

    def draw(self, event=None):
        if self.canvas: self.canvas.get_tk_widget().destroy()
        if self.toolbar: self.toolbar.destroy()

        try:
            period_val = self.period_map_ui.get(self.var_period.get(), "5y")
            interval_val = self.interval_map.get(self.var_interval.get(), "1d")
            
            df = yf.Ticker(self.symbol).history(period=period_val, interval=interval_val, auto_adjust=False)
            df = fillna_ohlcv(df)
            if df.empty: 
                tk.Label(self.canvas_frame, text="無有效資料").pack()
                return
                
            df = calc_macd(calc_kd(df))
            
            add_plots = []
            current_panel = 1 + int(self.var_vol.get())
            
            if self.var_kd.get():
                add_plots.extend([
                    mpf.make_addplot(df['K'], panel=current_panel, color='orange', title='KD'), 
                    mpf.make_addplot(df['D'], panel=current_panel, color='purple')
                ])
                current_panel += 1
                
            if self.var_macd.get():
                colors = ['r' if v >= 0 else 'g' for v in df['OSC']]
                add_plots.extend([
                    mpf.make_addplot(df['OSC'], type='bar', panel=current_panel, color=colors, title='MACD'), 
                    mpf.make_addplot(df['DIF'], panel=current_panel, color='orange'), 
                    mpf.make_addplot(df['DEM'], panel=current_panel, color='blue')
                ])

            mc = mpf.make_marketcolors(up='r', down='g', inherit=True)
            s = mpf.make_mpf_style(base_mpf_style='yahoo', marketcolors=mc)

            fig, _ = mpf.plot(
                df, type='candle', mav=(5, 10, 20, 40, 60), 
                volume=self.var_vol.get(), addplot=add_plots, style=s, 
                title=f"{self.code} {self.name}", returnfig=True, figsize=(12, 8), 
                panel_ratios=(2, 1) if not add_plots and self.var_vol.get() else None
            )
            
            self.canvas = FigureCanvasTkAgg(fig, master=self.canvas_frame)
            self.canvas.draw()
            self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            
            self.toolbar = NavigationToolbar2Tk(self.canvas, self.canvas_frame)
            self.toolbar.update()
            self.canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            
        except Exception as e: 
            tk.Label(self.canvas_frame, text=f"繪圖錯誤: {e}").pack()


# ==========================================
# 4. 介面層
# ==========================================
class StockApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Yahoo 終極選股 (終極完整排版版)")
        self.root.geometry("1200x900")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.cm = CategoryManager()
        self.analyzer = StockAnalyzer()
        self.is_running = False
        self.default_config = "stock_config.json"
        
        self._build_ui()
        self.load_from_file(self.default_config, silent=True)

    def on_closing(self):
        if messagebox.askokcancel("退出", "確定要關閉程式嗎？"): 
            self.is_running = False
            self.root.destroy()
            os._exit(0)

    def _safe_ui_update(self, func, *args, **kwargs): 
        self.root.after(0, lambda: func(*args, **kwargs))

    def sort_tree(self, col, descending):
        l = [(self.tree.set(k, col), k) for k in self.tree.get_children('')]
        l.sort(key=lambda t: float(t[0]) if col == 'price' else t[0], reverse=descending)
        for index, (_, k) in enumerate(l): 
            self.tree.move(k, '', index)
        self.tree.heading(col, command=lambda: self.sort_tree(col, not descending))

    def update_cnt(self, event=None):
        count = len(self.cm.get_stocks(self.cb_m.get(), self.cb_i.get()))
        self.lbl_cnt.config(text=f"({count})")

    def _build_ui(self):
        self.sv = tk.StringVar(value="準備就緒")
        f_status = ttk.Frame(self.root, relief="sunken")
        f_status.pack(side="bottom", fill="x")
        
        self.pb = ttk.Progressbar(f_status, mode="determinate")
        self.pb.pack(fill="x", side="top")
        ttk.Label(f_status, textvariable=self.sv, anchor="w").pack(fill="x", side="bottom")

        top = ttk.Frame(self.root, padding=10)
        top.pack(side="top", fill="x")
        
        ttk.Label(top, text="市場:").pack(side="left")
        self.cb_m = ttk.Combobox(top, values=["全部 (上市+上櫃)", "上市", "上櫃"], width=15, state="readonly")
        self.cb_m.current(0)
        self.cb_m.pack(side="left", padx=5)
        self.cb_m.bind("<<ComboboxSelected>>", self.on_market_change)
        
        ttk.Label(top, text="產業:").pack(side="left")
        self.cb_i = ttk.Combobox(top, width=15, state="readonly")
        self.cb_i.pack(side="left", padx=5)
        self.cb_i.bind("<<ComboboxSelected>>", self.update_cnt)
        
        self.lbl_cnt = ttk.Label(top, text="(0)", foreground="blue")
        self.lbl_cnt.pack(side="left")

        self.btn_run = ttk.Button(top, text="▶ 開始", command=self.run)
        self.btn_run.pack(side="left", padx=20)
        
        self.btn_stop = ttk.Button(top, text="⏹ 停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")
        
        self.var_refine = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="從結果再篩選", variable=self.var_refine).pack(side="left", padx=10)

        f_sets = ttk.Frame(top)
        f_sets.pack(side="right")
        ttk.Button(f_sets, text="💾 匯出設定", command=self.export_config).pack(side="left", padx=5)
        ttk.Button(f_sets, text="📂 匯入設定", command=self.import_config).pack(side="left", padx=5)

        self.paned = ttk.PanedWindow(self.root, orient="vertical")
        self.paned.pack(fill="both", expand=True)
        
        f_settings_container = ttk.Frame(self.paned)
        self.paned.add(f_settings_container, weight=1)

        sb_h = ttk.Scrollbar(f_settings_container, orient="horizontal")
        sb_v = ttk.Scrollbar(f_settings_container, orient="vertical")
        self.canvas = tk.Canvas(f_settings_container, highlightthickness=0, yscrollcommand=sb_v.set, xscrollcommand=sb_h.set)
        
        sb_v.config(command=self.canvas.yview)
        sb_h.config(command=self.canvas.xview)
        sb_h.pack(side="bottom", fill="x")
        sb_v.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.scrollable_frame = ttk.Frame(self.canvas)
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        
        self._bind_recursive(self.scrollable_frame)

        f_cond = ttk.LabelFrame(self.scrollable_frame, text="篩選條件設定", padding=10)
        f_cond.pack(fill="both", expand=True, padx=10, pady=5)
        
        for i in range(3): 
            f_cond.columnconfigure(i, weight=1)

        # ================= 第一欄 (LEFT) =================
        f_vol = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_vol.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        self.var_vol_en = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_vol, text="成交量", variable=self.var_vol_en, width=8).pack(side="left")
        self.cb_vol_tf = ttk.Combobox(f_vol, values=["日線(D)", "周線(W)", "月線(M)"], width=5, state="readonly")
        self.cb_vol_tf.current(1)
        self.cb_vol_tf.pack(side="left", padx=2)
        ttk.Label(f_vol, text="近").pack(side="left")
        self.entry_vol_n = ttk.Entry(f_vol, width=3)
        self.entry_vol_n.insert(0, "2")
        self.entry_vol_n.pack(side="left")
        ttk.Label(f_vol, text="根總和").pack(side="left")
        self.cb_vol_op = ttk.Combobox(f_vol, values=[">=", "<="], width=4, state="readonly")
        self.cb_vol_op.current(0)
        self.cb_vol_op.pack(side="left", padx=2)
        self.entry_vol_val = ttk.Entry(f_vol, width=6)
        self.entry_vol_val.insert(0, "2000")
        self.entry_vol_val.pack(side="left")
        ttk.Label(f_vol, text="張").pack(side="left")

        f_row1 = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_row1.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        self.var_price_en = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_row1, text="價格", variable=self.var_price_en, width=8).pack(side="left")
        ttk.Label(f_row1, text="現價").pack(side="left")
        self.cb_price_op = ttk.Combobox(f_row1, values=["<=", ">="], width=4, state="readonly")
        self.cb_price_op.current(0)
        self.cb_price_op.pack(side="left", padx=2)
        self.entry_price = ttk.Entry(f_row1, width=8)
        self.entry_price.insert(0, "100")
        self.entry_price.pack(side="left", padx=2)
        ttk.Label(f_row1, text="元").pack(side="left")

        f_macd = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_macd.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        self.var_macd_en = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_macd, text="MACD", variable=self.var_macd_en, width=8).pack(side="left")
        ttk.Label(f_macd, text="(紅柱)", foreground="red", font=("Arial", 9, "bold")).pack(side="left", padx=2)
        self.cb_macd_tf = ttk.Combobox(f_macd, values=["日線(D)", "周線(W)", "月線(M)"], width=5, state="readonly")
        self.cb_macd_tf.current(0)
        self.cb_macd_tf.pack(side="left", padx=2)
        self.cb_macd_cond = ttk.Combobox(f_macd, values=["MACD 柱狀體 > 0 (紅柱)"], width=20, state="readonly")
        self.cb_macd_cond.current(0)
        self.cb_macd_cond.pack(side="left", padx=2)

        f_kd_combined = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_kd_combined.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        
        self.kd_uis = []
        for i, val in enumerate(["40", "20"]):
            ui_dict = self._create_kd_row(f_kd_combined, f"KD({i+1})", i+1, val)
            self.kd_uis.append(ui_dict)

        # ================= 第二欄 (MIDDLE) =================
        f_gap_container = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_gap_container.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        
        self.gap_uis = []
        for i in range(2):
            ui_dict = self._create_gap_row(f_gap_container, f"均線糾結{i+1}", i, "MA5", "MA10", "3")
            self.gap_uis.append(ui_dict)

        f_trend_container = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_trend_container.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=5, pady=5)
        
        self.cond_uis = []
        trend_configs = [
            ("日", "D", ["MA5", "MA10", "MA20", "MA60", "MA120"], "MA60"),
            ("周", "W", ["MA5", "MA10", "MA20", "MA40"], "MA20"),
            ("月", "M", ["MA5", "MA10", "MA20", "MA40", "MA60"], "MA20")
        ]
        for t, c, ms, m0 in trend_configs:
            ui_dict = self._create_row(f_trend_container, f"{t}線趨勢", c, ["Close"], ms, [">=", "<="], m0)
            self.cond_uis.append(ui_dict)

        f_bias_container = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_bias_container.grid(row=3, column=1, sticky="nsew", padx=5, pady=5)
        
        self.bias_uis = []
        bias_configs = [
            ("日", "D", "MA60", "3"),
            ("周", "W", "MA20", "3"),
            ("月", "M", "MA5", "5")
        ]
        for t, c, m, v in bias_configs:
            ui_dict = self._create_bias_row_simple(f_bias_container, f"{t}線乖離", c, m, v)
            self.bias_uis.append(ui_dict)

        # ================= 第三欄 (RIGHT) =================
        f_cons = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_cons.grid(row=0, column=2, sticky="ew", padx=5, pady=5)
        self.var_cons_en = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_cons, text="連續趨勢", variable=self.var_cons_en, width=8).pack(side="left")
        self.cb_cons_tf = ttk.Combobox(f_cons, values=["日線(D)", "周線(W)", "月線(M)"], width=5, state="readonly")
        self.cb_cons_tf.current(1)
        self.cb_cons_tf.pack(side="left", padx=2)
        ttk.Label(f_cons, text="連").pack(side="left")
        self.entry_cons_n = ttk.Entry(f_cons, width=3)
        self.entry_cons_n.insert(0, "5")
        self.entry_cons_n.pack(side="left")
        ttk.Label(f_cons, text="根收").pack(side="left")
        self.cb_cons_op = ttk.Combobox(f_cons, values=[">=", "<="], width=4, state="readonly")
        self.cb_cons_op.current(0)
        self.cb_cons_op.pack(side="left")
        self.cb_cons_ma = ttk.Combobox(f_cons, values=["MA5", "MA10", "MA20", "MA60"], width=6, state="readonly")
        self.cb_cons_ma.current(0)
        self.cb_cons_ma.pack(side="left", padx=2)

        f_ret = ttk.Frame(f_cond, relief="groove", borderwidth=1, padding=5)
        f_ret.grid(row=1, column=2, sticky="ew", padx=5, pady=5)
        self.var_ret_en = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_ret, text="波段回檔 (收盤價計算)", variable=self.var_ret_en, width=20).pack(side="left")
        self.cb_ret_tf = ttk.Combobox(f_ret, values=["日線(D)", "周線(W)", "月線(M)"], width=5, state="readonly")
        self.cb_ret_tf.current(1)
        self.cb_ret_tf.pack(side="left", padx=2)
        ttk.Label(f_ret, text="近").pack(side="left")
        self.entry_ret_n = ttk.Entry(f_ret, width=3)
        self.entry_ret_n.insert(0, "10")
        self.entry_ret_n.pack(side="left")
        ttk.Label(f_ret, text="根 (最高收-後續最低收)/最高收").pack(side="left")
        self.cb_ret_op = ttk.Combobox(f_ret, values=[">=", "<="], width=4, state="readonly")
        self.cb_ret_op.current(0)
        self.cb_ret_op.pack(side="left")
        self.entry_ret_val = ttk.Entry(f_ret, width=5)
        self.entry_ret_val.insert(0, "10")
        self.entry_ret_val.pack(side="left")
        ttk.Label(f_ret, text="%").pack(side="left")

        f_breakout = ttk.LabelFrame(f_cond, text="周K突破 (前周收<MA 且 當周收>MA)", padding=5)
        f_breakout.grid(row=2, column=2, sticky="nsew", padx=5, pady=5)
        
        self.bk_vars = {}
        for k in [5, 10, 20]:
            var = tk.BooleanVar(value=False)
            self.bk_vars[k] = var
            ttk.Checkbutton(f_breakout, text=f"{k}MA", variable=var).pack(side="left", padx=5, expand=True)

        # Result Tree
        f_res = ttk.LabelFrame(self.paned, text="篩選結果 (點兩下開啟互動圖表，點標題可排序)", padding=5)
        self.paned.add(f_res, weight=1)
        
        self.tree = ttk.Treeview(f_res, columns=("id", "name", "type", "industry", "price", "match"), show="headings")
        self.tree.tag_configure('matched', background='#FFFACD', foreground='red')
        self.tree.tag_configure('normal', background='white', foreground='black')
        
        col_settings = [
            ("id", "代號", 60), ("name", "名稱", 80), ("type", "市場", 60), 
            ("industry", "產業", 100), ("price", "現價", 70), ("match", "符合細節", 600)
        ]
        for col, text, width in col_settings:
            self.tree.heading(col, text=text, command=lambda c=col: self.sort_tree(c, False))
            self.tree.column(col, width=width, anchor="w" if col=="match" else "center")
            
        sb = ttk.Scrollbar(f_res, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        
        self.tree.bind("<Double-1>", self.on_tree_double_click)

        self.on_market_change(None)

    def _bind_recursive(self, widget):
        widget.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
        for child in widget.winfo_children(): 
            self._bind_recursive(child)

    def on_tree_double_click(self, event):
        item = self.tree.selection()
        if item:
            vals = self.tree.item(item[0], "values")
            ChartPopup(self.root, vals[0], vals[1], vals[2])

    def _create_kd_row(self, parent, title, def_tf_idx, def_val):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        
        res = {
            'en': tk.BooleanVar(value=False), 
            'k_gt_d': tk.BooleanVar(value=False)
        }
        
        ttk.Checkbutton(f, text=title, variable=res['en'], width=8).pack(side="left")
        res['tf'] = ttk.Combobox(f, values=["日線(D)", "周線(W)", "月線(M)"], width=5, state="readonly")
        res['tf'].current(def_tf_idx)
        res['tf'].pack(side="left", padx=2)
        
        ttk.Label(f, text="9K").pack(side="left")
        res['op'] = ttk.Combobox(f, values=["<=", ">="], width=4, state="readonly")
        res['op'].current(1)
        res['op'].pack(side="left")
        
        res['val'] = ttk.Entry(f, width=3)
        res['val'].insert(0, def_val)
        res['val'].pack(side="left")
        
        ttk.Label(f, text="且差距<").pack(side="left")
        res['gap'] = ttk.Entry(f, width=3)
        res['gap'].insert(0, "5")
        res['gap'].pack(side="left")
        
        ttk.Checkbutton(f, text="且近", variable=res['k_gt_d']).pack(side="left", padx=(5, 0))
        res['gt_d_n'] = ttk.Entry(f, width=3)
        res['gt_d_n'].insert(0, "3")
        res['gt_d_n'].pack(side="left")
        
        ttk.Label(f, text="根維持 9K>9D, 容錯").pack(side="left")
        res['tol'] = ttk.Entry(f, width=3)
        res['tol'].insert(0, "2.0")
        res['tol'].pack(side="left")
        
        return res

    def _create_bias_row_simple(self, parent, title, tf_code, def_ma, def_val):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        
        res = {'en': tk.BooleanVar(value=False), 'tf': tf_code}
        ttk.Checkbutton(f, text=title, variable=res['en'], width=10).pack(side="left")
        
        res['ma'] = ttk.Combobox(f, values=["MA5", "MA10", "MA20", "MA40", "MA60", "MA120"], width=6, state="readonly")
        res['ma'].set(def_ma)
        res['ma'].pack(side="left", padx=2)
        
        ttk.Label(f, text="差(abs)<").pack(side="left")
        res['val'] = ttk.Entry(f, width=4)
        res['val'].insert(0, def_val)
        res['val'].pack(side="left", padx=2)
        ttk.Label(f, text="%").pack(side="left")
        
        return res

    def _create_gap_row(self, parent, title, def_tf_idx, def_ma1, def_ma2, def_val):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        
        res = {'en': tk.BooleanVar(value=False)}
        ttk.Checkbutton(f, text=title, variable=res['en'], width=10).pack(side="left")
        
        res['tf'] = ttk.Combobox(f, values=["日線(D)", "周線(W)", "月線(M)"], width=5, state="readonly")
        res['tf'].current(def_tf_idx)
        res['tf'].pack(side="left", padx=2)
        
        res['ma1'] = ttk.Combobox(f, values=["MA5", "MA10", "MA20", "MA40", "MA60", "MA120"], width=5, state="readonly")
        res['ma1'].set(def_ma1)
        res['ma1'].pack(side="left", padx=2)
        
        ttk.Label(f, text="與").pack(side="left")
        res['ma2'] = ttk.Combobox(f, values=["MA5", "MA10", "MA20", "MA40", "MA60", "MA120"], width=5, state="readonly")
        res['ma2'].set(def_ma2)
        res['ma2'].pack(side="left", padx=2)
        
        ttk.Label(f, text="差<").pack(side="left")
        res['val'] = ttk.Entry(f, width=3)
        res['val'].insert(0, def_val)
        res['val'].pack(side="left", padx=2)
        ttk.Label(f, text="%").pack(side="left")
        
        return res

    def _create_row(self, parent, title, tf_code, opts_l, opts_r, ops, default_r):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        
        res = {'en': tk.BooleanVar(value=False), 'tf': tf_code}
        ttk.Checkbutton(f, text=f"{title}", variable=res['en'], width=10).pack(side="left")
        
        res['l'] = ttk.Combobox(f, values=opts_l, width=5, state="readonly")
        res['l'].current(0)
        res['l'].pack(side="left", padx=5)
        
        res['op'] = ttk.Combobox(f, values=ops, width=4, state="readonly")
        res['op'].current(0)
        res['op'].pack(side="left", padx=5)
        
        res['r'] = ttk.Combobox(f, values=opts_r, width=6, state="readonly")
        res['r'].set(default_r)
        res['r'].pack(side="left", padx=5)
        
        return res

    def _config_spec(self):
        spec = [
            ("market", "cb", self.cb_m, "全部 (上市+上櫃)"), 
            ("industry", "cb", self.cb_i, "全部產業"),
            ("vol_en", "bv", self.var_vol_en, True), 
            ("vol_tf", "cb", self.cb_vol_tf, "周線(W)"), 
            ("vol_n", "entry", self.entry_vol_n, "2"), 
            ("vol_op", "cb", self.cb_vol_op, ">="), 
            ("vol_val", "entry", self.entry_vol_val, "2000"),
            ("price_en", "bv", self.var_price_en, False), 
            ("price_op", "cb", self.cb_price_op, "<="), 
            ("price_val", "entry", self.entry_price, "100"),
            ("macd_en", "bv", self.var_macd_en, False), 
            ("macd_tf", "cb", self.cb_macd_tf, "日線(D)"), 
            ("macd_cond", "cb", self.cb_macd_cond, "MACD 柱狀體 > 0 (紅柱)"),
            ("cons_en", "bv", self.var_cons_en, False), 
            ("cons_tf", "cb", self.cb_cons_tf, "周線(W)"), 
            ("cons_n", "entry", self.entry_cons_n, "5"), 
            ("cons_op", "cb", self.cb_cons_op, ">="), 
            ("cons_ma", "cb", self.cb_cons_ma, "MA5"),
            ("ret_en", "bv", self.var_ret_en, False), 
            ("ret_tf", "cb", self.cb_ret_tf, "周線(W)"), 
            ("ret_n", "entry", self.entry_ret_n, "10"), 
            ("ret_op", "cb", self.cb_ret_op, ">="), 
            ("ret_val", "entry", self.entry_ret_val, "10"),
        ]
        
        for i, ui in enumerate(self.kd_uis): 
            spec.extend([
                (f"kd{i+1}_en", "bv", ui['en'], False), 
                (f"kd{i+1}_tf", "cb", ui['tf'], ["日線(D)","周線(W)","月線(M)"][i]), 
                (f"kd{i+1}_op", "cb", ui['op'], ">="), 
                (f"kd{i+1}_val", "entry", ui['val'], ["40","20"][i]), 
                (f"kd{i+1}_gap", "entry", ui['gap'], "5"), 
                (f"kd{i+1}_k_gt_d", "bv", ui['k_gt_d'], False), 
                (f"kd{i+1}_gt_d_n", "entry", ui['gt_d_n'], "3"), 
                (f"kd{i+1}_tol", "entry", ui['tol'], "2.0")
            ])
            
        for i, ui in enumerate(self.bias_uis): 
            spec.extend([
                (f"bias_{ui['tf'].lower()}_en", "bv", ui['en'], False), 
                (f"bias_{ui['tf'].lower()}_ma", "cb", ui['ma'], ["MA60","MA20","MA5"][i]), 
                (f"bias_{ui['tf'].lower()}_val", "entry", ui['val'], ["3","3","5"][i])
            ])
            
        for i, ui in enumerate(self.gap_uis): 
            spec.extend([
                (f"gap{i+1}_en", "bv", ui['en'], False), 
                (f"gap{i+1}_tf", "cb", ui['tf'], ["日線(D)","周線(W)"][i]), 
                (f"gap{i+1}_ma1", "cb", ui['ma1'], "MA5"), 
                (f"gap{i+1}_ma2", "cb", ui['ma2'], "MA10"), 
                (f"gap{i+1}_val", "entry", ui['val'], "3")
            ])
            
        for i, ui in enumerate(self.cond_uis): 
            spec.extend([
                (f"trend_{ui['tf'].lower()}_en", "bv", ui['en'], False), 
                (f"trend_{ui['tf'].lower()}_op", "cb", ui['op'], ">="), 
                (f"trend_{ui['tf'].lower()}_r", "cb", ui['r'], ["MA60","MA20","MA20"][i])
            ])
            
        for k, v in self.bk_vars.items(): 
            spec.append((f"bk_{k}", "bv", v, False))
            
        return spec

    def _get_current_config_dict(self): 
        result = {}
        for k, wt, w, d in self._config_spec():
            result[k] = w.get()
        return result

    def _apply_config_dict(self, config):
        for k, wt, w, d in self._config_spec():
            v = config.get(k, d)
            if wt in ("bv", "cb"): 
                w.set(v)
            if k == "market": 
                self.cb_i.config(values=self.cm.get_industries(self.cb_m.get()))
                self.update_cnt()
            if wt == "entry": 
                w.delete(0, "end")
                w.insert(0, v)
        self.update_cnt()

    def export_config(self):
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")], title="另存策略設定")
        if path:
            try:
                config_data = self._get_current_config_dict()
                with open(path, "w", encoding="utf-8") as f: 
                    json.dump(config_data, f, indent=4)
                messagebox.showinfo("成功", f"策略已儲存至：\n{os.path.basename(path)}")
                with open(self.default_config, "w", encoding="utf-8") as f: 
                    json.dump(config_data, f, indent=4)
            except Exception as e: 
                messagebox.showerror("錯誤", f"儲存失敗: {e}")

    def import_config(self):
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")], title="選取策略設定檔")
        if path: 
            self.load_from_file(path)

    def load_from_file(self, path, silent=False):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f: 
                    self._apply_config_dict(json.load(f))
                if not silent: messagebox.showinfo("成功", f"設定已載入！")
            except Exception as e:
                if not silent: messagebox.showerror("錯誤", f"讀取失敗: {e}")

    def on_market_change(self, e):
        self.cb_i.config(values=self.cm.get_industries(self.cb_m.get()))
        self.cb_i.current(0)
        self.update_cnt()

    def stop(self):
        self.is_running = False
        self.sv.set("停止中... (請稍候，等待當前下載完成)")
        self.btn_stop.config(state="disabled")

    def run(self):
        try:
            with open(self.default_config, "w", encoding="utf-8") as f: 
                json.dump(self._get_current_config_dict(), f, indent=4)
        except: pass
        
        self.is_running = True
        self.btn_run.config(state="disabled")
        self.btn_stop.config(state="normal")
        threading.Thread(target=self._worker, daemon=True).start()

    def _collect_kd_config(self, ui_dict):
        if not ui_dict['en'].get(): return None
        return {
            'tf': tf_text_to_code(ui_dict['tf'].get()), 
            'k_op': ui_dict['op'].get(), 
            'k_val': float(ui_dict['val'].get()), 
            'gap': float(ui_dict['gap'].get()), 
            'k_gt_d': ui_dict['k_gt_d'].get(), 
            'k_gt_d_n': int(ui_dict['gt_d_n'].get()), 
            'tolerance': float(ui_dict['tol'].get())
        }

    def _worker(self):
        is_refine = self.var_refine.get()
        stock_list = []
        tree_map = {}
        
        if is_refine:
            for item in self.tree.get_children():
                v = self.tree.item(item)['values']
                stock_list.append((str(v[0]), str(v[1]), str(v[2]), str(v[3])))
                tree_map[str(v[0])] = item
        else: 
            self._safe_ui_update(lambda: self.tree.delete(*self.tree.get_children()))
            stock_list = self.cm.get_stocks(self.cb_m.get(), self.cb_i.get())

        self._safe_ui_update(lambda: self.pb.configure(maximum=len(stock_list)))

        # 收集變數設定
        p_cfg = (float(self.entry_price.get()), self.cb_price_op.get()) if self.var_price_en.get() else None
        
        b_cfgs = []
        for b in self.bias_uis:
            if b['en'].get():
                b_cfgs.append({'tf': b['tf'], 'ma': b['ma'].get(), 'limit': float(b['val'].get())})
                
        g_cfgs = []
        for g in self.gap_uis:
            if g['en'].get():
                g_cfgs.append({'tf': tf_text_to_code(g['tf'].get()), 'ma1': g['ma1'].get(), 'ma2': g['ma2'].get(), 'limit': float(g['val'].get())})
                
        conds_t = []
        for x in self.cond_uis:
            if x['en'].get():
                conds_t.append({'tf': x['tf'], 'left': x['l'].get(), 'op': x['op'].get(), 'right': x['r'].get()})
                
        v_cfg = {'tf': tf_text_to_code(self.cb_vol_tf.get()), 'n': int(self.entry_vol_n.get()), 'limit': float(self.entry_vol_val.get()), 'op': self.cb_vol_op.get()} if self.var_vol_en.get() else None
        macd_cfg = {'tf': tf_text_to_code(self.cb_macd_tf.get()), 'osc_only_red': True} if self.var_macd_en.get() else None
        c_cfg = {'tf': tf_text_to_code(self.cb_cons_tf.get()), 'n': int(self.entry_cons_n.get()), 'op': self.cb_cons_op.get(), 'ma': self.cb_cons_ma.get()} if self.var_cons_en.get() else None
        r_cfg = {'tf': tf_text_to_code(self.cb_ret_tf.get()), 'n': int(self.entry_ret_n.get()), 'op': self.cb_ret_op.get(), 'limit': float(self.entry_ret_val.get())} if self.var_ret_en.get() else None
        bk_any = any(v.get() for v in self.bk_vars.values())
        
        kd_cfgs = [self._collect_kd_config(ui) for ui in self.kd_uis]

        matches = 0
        for i, (code, name, m_type, ind_name) in enumerate(stock_list):
            if not self.is_running: break
            
            self._safe_ui_update(lambda c=code, n=name, idx=i+1: (
                self.sv.set(f"分析: {c} {n} ({idx}/{len(stock_list)})"), 
                self.pb.configure(value=idx)
            ))

            ok, msg = self.analyzer.fetch_data(code, m_type)
            vld = False
            note = []
            cur = 0

            if ok and self.analyzer.process_timeframes():
                vld = True
                cur = self.analyzer.get_current_price()
                
                if p_cfg:
                    if (p_cfg[1] == "<=" and cur > p_cfg[0]) or (p_cfg[1] == ">=" and cur < p_cfg[0]): 
                        vld = False
                    else: 
                        note.append(f"Price{p_cfg[1]}{p_cfg[0]}")
                        
                if vld and v_cfg and not self.analyzer.check_volume(v_cfg['tf'], v_cfg['n'], v_cfg['limit'], v_cfg['op']): 
                    vld = False
                    
                if vld and macd_cfg:
                    is_ok, m_msg = self.analyzer.check_macd(macd_cfg['tf'], macd_cfg['osc_only_red'])
                    if not is_ok: 
                        vld = False
                    else: 
                        note.append(m_msg)
                
                for idx, k_cfg in enumerate(kd_cfgs):
                    if vld and k_cfg:
                        is_k_ok, k_msg = self.analyzer.check_kd_advanced(**k_cfg)
                        if not is_k_ok: 
                            vld = False
                        else: 
                            note.append(f"KD{'' if idx==0 else '2'}:{k_msg}")

                if vld:
                    for c in g_cfgs:
                        if not self.analyzer.check_ma_gap(c['tf'], c['ma1'], c['ma2'], c['limit']): 
                            vld = False
                            break
                        note.append(f"Gap({c['tf']}.{c['ma1']}-{c['ma2']})<{c['limit']}%")
                        
                if vld and conds_t:
                    if not self.analyzer.check_conditions(conds_t): 
                        vld = False
                    else: 
                        note.extend([f"{c['tf']}.{c['op']}{c['right']}" for c in conds_t])
                        
                if vld:
                    for c in b_cfgs:
                        is_ok, msg = self.analyzer.check_price_bias_abs(c['tf'], c['ma'], c['limit'])
                        if not is_ok: 
                            vld = False
                            break
                        note.append(msg)
                        
                if vld and c_cfg:
                    is_ok, msg = self.analyzer.check_consecutive_trend(**c_cfg)
                    if not is_ok: 
                        vld = False
                    else: 
                        note.append(msg)
                        
                if vld and r_cfg:
                    is_ok, msg = self.analyzer.check_retracement_close_only(**r_cfg)
                    if not is_ok: 
                        vld = False
                    else: 
                        note.append(msg)
                        
                if vld and bk_any:
                    is_ok, msg = self.analyzer.check_flexible_breakout(*[v.get() for v in self.bk_vars.values()])
                    if not is_ok: 
                        vld = False
                    else: 
                        note.append(msg)

                if not (p_cfg or g_cfgs or b_cfgs or conds_t or v_cfg or any(kd_cfgs) or macd_cfg or c_cfg or r_cfg or bk_any): 
                    vld = False

            def update_t(c=code, n=name, mt=m_type, ind=ind_name, cr=cur, v=vld, nts=note):
                p_disp = round(cr, 2) if pd.notna(cr) else 0.0
                if is_refine:
                    if c in tree_map: 
                        if v:
                            self.tree.item(tree_map[c], values=(c, n, mt, ind, p_disp, " & ".join(nts)), tags=('matched',))
                        else:
                            self.tree.item(tree_map[c], values=(c, n, mt, ind, p_disp, ""), tags=('normal',))
                else:
                    if v: 
                        self.tree.insert("", "end", values=(c, n, mt, ind, p_disp, " & ".join(nts)), tags=('normal',))
                        self.tree.yview_moveto(1)

            if vld: 
                matches += 1
            self._safe_ui_update(update_t)

        self.is_running = False
        self._safe_ui_update(lambda: self.btn_run.config(state="normal"))
        self._safe_ui_update(lambda: self.btn_stop.config(state="disabled"))
        f_msg = f"標記完成，當前符合的有 {matches} 檔" if is_refine else f"完成，找到 {matches} 檔"
        self._safe_ui_update(lambda: self.sv.set(f_msg))
        self._safe_ui_update(lambda: messagebox.showinfo("完成", f_msg))

if __name__ == "__main__":
    root = tk.Tk()
    app = StockApp(root)
    root.mainloop()