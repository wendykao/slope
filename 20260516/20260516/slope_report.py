# slope_report.py
# -*- coding: utf-8 -*-
"""
Slope 策略互動式回測報表產生器
---
公開介面:
  build_opt_report(...)           → 最佳化回測 HTML 報表（含最佳參數完整回測）
  build_backtest_report(...)  → 單次回測 HTML 報表
"""

import os, webbrowser
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ═══════════════════════════════════════════════════════════════════════════════
# §1  格式化工具
# ═══════════════════════════════════════════════════════════════════════════════

def _p(v, d=2):
    """數值 → 百分比字串 (e.g. 0.123 → '12.30%')"""
    try:
        if pd.isna(v): return 'N/A'
    except Exception: pass
    return f"{float(v)*100:.{d}f}%"

def _n(v, d=2):
    """數值 → 固定小數字串"""
    try:
        if pd.isna(v): return 'N/A'
    except Exception: pass
    return f"{float(v):.{d}f}"

def _a(v, d=0):
    """數值 → 千位分隔字串"""
    try:
        if pd.isna(v): return 'N/A'
    except Exception: pass
    return f"{float(v):,.{d}f}"

def _dt(v):
    """日期/NaT → YYYY-MM-DD 字串"""
    try:
        if pd.isna(v): return '─'
    except Exception: pass
    if hasattr(v, 'strftime'): return v.strftime('%Y-%m-%d')
    return str(v)[:10]

def _cls(v, good_pos=True):
    """數值 → CSS class (pos/neg/'')"""
    try:
        f = float(v)
        if np.isnan(f): return ''
        if f > 0: return 'pos' if good_pos else 'neg'
        if f < 0: return 'neg' if good_pos else 'pos'
    except Exception: pass
    return ''

def _sg(v):
    """取 Series/scalar 安全值"""
    try:
        if isinstance(v, pd.Series): v = v.iloc[0]
        if pd.isna(v): return np.nan
        return float(v)
    except Exception: return np.nan


# ═══════════════════════════════════════════════════════════════════════════════
# §2  交易紀錄計算
# ═══════════════════════════════════════════════════════════════════════════════

def _prep_trade(trade):
    """確保 trade DataFrame 含 date 欄（非 index）。"""
    if trade is None: return pd.DataFrame(columns=['date','stk_bbg','t_shares','t_amt'])
    if isinstance(trade, list) and len(trade) == 0:
        return pd.DataFrame(columns=['date','stk_bbg','t_shares','t_amt'])
    tr = trade.copy()
    if tr.index.name == 'date' or (not isinstance(tr.index, pd.RangeIndex) and 'date' not in tr.columns):
        tr = tr.reset_index()
    return tr

def _prep_hp(hp):
    """確保 hold_portfolio 有標準欄位且含 date 欄。"""
    if hp is None: return pd.DataFrame(columns=['date','stk_bbg','signal','indicator'])
    if isinstance(hp, list) and len(hp) == 0:
        return pd.DataFrame(columns=['date','stk_bbg','signal','indicator'])
    h = hp.copy()
    if isinstance(h.index, pd.DatetimeIndex):
        h = h.reset_index()
    if 'date' not in h.columns:
        h = h.reset_index()
    return h


def _compute_trade_log(hold_portfolio, trade):
    """
    從 hold_portfolio 與 trade 推導每筆進出場明細。

    回傳 DataFrame 欄位:
        Ticker, 入場日, 出場日, 持有天數, 入場金額, 出場金額, 損益, 報酬率, 勝負
    """
    hp = _prep_hp(hold_portfolio)
    tr = _prep_trade(trade)
    if hp.empty: return pd.DataFrame()

    records, entry_dict = [], {}
    for date in sorted(hp['date'].unique()):
        today = set(hp.loc[hp['date'] == date, 'stk_bbg'].tolist())
        prev  = set(entry_dict.keys())

        for stk in (today - prev):
            row = tr[(tr['date'] == date) & (tr['stk_bbg'] == stk)]
            amt = float(row['t_amt'].iloc[0]) if not row.empty else np.nan
            entry_dict[stk] = {'entry_date': date, 'entry_amt': amt}

        for stk in (prev - today):
            info     = entry_dict.pop(stk)
            row      = tr[(tr['date'] == date) & (tr['stk_bbg'] == stk)]
            exit_amt = abs(float(row['t_amt'].iloc[0])) if not row.empty else np.nan
            cost     = info['entry_amt']
            profit   = (exit_amt - cost) if (pd.notna(exit_amt) and pd.notna(cost)) else np.nan
            ret      = profit / abs(cost) if (pd.notna(profit) and cost) else np.nan
            days     = int((date - info['entry_date']) / np.timedelta64(1, 'D'))
            records.append(dict(
                Ticker=stk, 入場日=info['entry_date'], 出場日=date,
                持有天數=days, 入場金額=cost, 出場金額=exit_amt,
                損益=profit, 報酬率=ret,
                勝負=('勝' if (pd.notna(profit) and profit > 0) else
                     '敗' if (pd.notna(profit) and profit < 0) else '平手')
            ))

    for stk, info in entry_dict.items():
        max_date = hp['date'].max()
        days = int((max_date - info['entry_date']) / np.timedelta64(1, 'D'))
        records.append(dict(
            Ticker=stk, 入場日=info['entry_date'], 出場日=None,
            持有天數=days, 入場金額=info['entry_amt'],
            出場金額=np.nan, 損益=np.nan, 報酬率=np.nan, 勝負='持有中'
        ))

    return pd.DataFrame(records)


def _compute_stock_profile(trade_log, hp_orig, trade):
    """
    每檔股票統計:
        買進次數、賣出次數、完成交易、勝出、勝率、平均持有天數、最新Slope分數、最新訊號
    """
    hp = _prep_hp(hp_orig)
    tr = _prep_trade(trade)
    if trade_log is None or trade_log.empty: return pd.DataFrame()

    rows = []
    for stk in sorted(trade_log['Ticker'].unique()):
        sub  = trade_log[trade_log['Ticker'] == stk]
        done = sub[sub['勝負'].isin(['勝', '敗', '平手'])]
        n_buy  = len(tr[(tr['stk_bbg'] == stk) & (tr['t_shares'] > 0)])
        n_sell = len(tr[(tr['stk_bbg'] == stk) & (tr['t_shares'] < 0)])
        n      = len(done)
        wins   = (done['勝負'] == '勝').sum()
        wr     = wins / n if n > 0 else np.nan
        ad     = done['持有天數'].mean() if not done.empty else np.nan
        hp_s   = hp[hp['stk_bbg'] == stk] if not hp.empty else pd.DataFrame()
        li     = float(hp_s.sort_values('date')['indicator'].iloc[-1]) if not hp_s.empty else np.nan
        rows.append(dict(
            Ticker=stk,
            買進次數=n_buy, 賣出次數=n_sell,
            完成交易=n, 勝出次數=wins,
            勝率=round(wr, 4)   if pd.notna(wr) else np.nan,
            平均持有天數=round(ad, 1) if pd.notna(ad) else np.nan,
            最新Slope分數=round(li, 6)  if pd.notna(li) else np.nan,
            最新訊號=('多頭' if pd.notna(li) and li > 0 else
                    '空頭' if pd.notna(li) and li < 0 else '─')
        ))

    return pd.DataFrame(rows).sort_values('勝率', ascending=False).reset_index(drop=True)


def _trade_summary(trade_log):
    """從 trade_log 計算簡要統計（回傳 dict）。"""
    if trade_log is None or trade_log.empty:
        return {}
    done = trade_log[trade_log['勝負'].isin(['勝', '敗', '平手'])]
    n    = len(done)
    if n == 0: return {}
    wins = (done['勝負'] == '勝').sum()
    lose = (done['勝負'] == '敗').sum()
    wr   = wins / n
    ad   = done['持有天數'].mean()
    md   = done['持有天數'].max()
    avg_ret_w = done.loc[done['勝負']=='勝','報酬率'].mean() if wins else np.nan
    avg_ret_l = done.loc[done['勝負']=='敗','報酬率'].mean() if lose else np.nan
    return dict(
        總交易次數=n, 勝出次數=int(wins), 虧損次數=int(lose),
        勝率=wr, 平均持有天數=round(ad,1), 最大持有天數=int(md),
        平均獲利率=avg_ret_w, 平均虧損率=avg_ret_l
    )


# ═══════════════════════════════════════════════════════════════════════════════
# §3  Plotly 圖表
# ═══════════════════════════════════════════════════════════════════════════════

# 黑底主題色彩配置 (與 _CSS 對應)
_BG_DARK    = '#0e1117'
_PANEL_DARK = '#1a2030'
_TEXT_DARK  = '#e6edf3'
_GRID_DARK  = '#2c3344'
_CLR_STRAT  = '#4ea1ff'   # 策略：藍
_CLR_BENCH  = '#ff9f43'   # S&P 500：橘
_CLR_POS    = '#22c55e'
_CLR_NEG    = '#ef4444'
_CLR_ALPHA_POS = '#22c55e'
_CLR_ALPHA_NEG = '#ef4444'

_LAYOUT = dict(
    template='plotly_dark',
    paper_bgcolor=_PANEL_DARK,
    plot_bgcolor=_PANEL_DARK,
    font=dict(family="'Segoe UI',Arial,sans-serif", size=12, color=_TEXT_DARK),
    margin=dict(l=60, r=20, t=50, b=42),
    legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                bgcolor='rgba(0,0,0,0)', font=dict(color=_TEXT_DARK)),
    xaxis=dict(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK),
    yaxis=dict(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK),
)


def _div(fig, h=None):
    if h: fig.update_layout(height=h)
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={'displayModeBar': True, 'responsive': True})


def _chart_cum_ret(perf):
    """累積報酬率 + 策略回撤雙列圖。"""
    d  = perf.index.tolist()
    pc = (perf['p_ret_cul'] - 1) * 100
    bc = (perf['b_ret_cul'] - 1) * 100
    rm = perf['p_ret_cul'].cummax()
    dd = (perf['p_ret_cul'] - rm) / rm * 100

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.68, 0.32],
        vertical_spacing=0.08,
        subplot_titles=['累積報酬率 (%)', '策略最大回撤 (%)']
    )
    fig.add_trace(go.Scatter(x=d, y=pc.tolist(), name='策略',
                             line=dict(color=_CLR_STRAT, width=2.4)), row=1, col=1)
    fig.add_trace(go.Scatter(x=d, y=bc.tolist(), name='S&P 500',
                             line=dict(color=_CLR_BENCH, width=1.9, dash='dot')), row=1, col=1)
    fig.add_trace(go.Scatter(x=d, y=dd.tolist(), name='回撤', showlegend=False,
                             fill='tozeroy', mode='lines',
                             line=dict(color=_CLR_NEG, width=1),
                             fillcolor='rgba(239,68,68,.18)'), row=2, col=1)
    fig.update_layout(**_LAYOUT, height=560)
    fig.update_xaxes(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK)
    fig.update_yaxes(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK)
    fig.update_yaxes(ticksuffix='%', row=1, col=1)
    fig.update_yaxes(ticksuffix='%', row=2, col=1)
    return _div(fig)


def _chart_annual(py):
    """逐年報酬率比較 + 每年 Alpha 雙列子圖。
    上：策略 vs S&P500 分組柱狀圖（顏色固定：藍 / 橘）
    下：每年 Alpha 柱狀圖（正綠負紅）
    """
    years = [str(y.year) for y in py.index]
    p_r   = (py['p_ret']  * 100).round(2).tolist()
    b_r   = (py['b_ret']  * 100).round(2).tolist()
    a_r   = (py['alpha']  * 100).round(2).tolist()
    a_clr = [_CLR_ALPHA_POS if v >= 0 else _CLR_ALPHA_NEG for v in a_r]

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, row_heights=[0.62, 0.38],
        vertical_spacing=0.12,
        subplot_titles=['策略 vs S&P 500 逐年報酬率 (%)', '每年 Alpha (策略 − S&P 500, %)']
    )
    fig.add_trace(go.Bar(name='策略', x=years, y=p_r,
                         marker_color=_CLR_STRAT,
                         text=[f"{v:.1f}%" for v in p_r],
                         textposition='outside',
                         textfont=dict(size=11, color=_TEXT_DARK)),
                  row=1, col=1)
    fig.add_trace(go.Bar(name='S&P 500', x=years, y=b_r,
                         marker_color=_CLR_BENCH,
                         text=[f"{v:.1f}%" for v in b_r],
                         textposition='outside',
                         textfont=dict(size=11, color=_TEXT_DARK)),
                  row=1, col=1)
    fig.add_trace(go.Bar(name='Alpha', x=years, y=a_r,
                         marker_color=a_clr, showlegend=False,
                         text=[f"{v:+.1f}%" for v in a_r],
                         textposition='outside',
                         textfont=dict(size=11, color=_TEXT_DARK)),
                  row=2, col=1)

    fig.update_layout(**_LAYOUT, barmode='group', height=620,
                      bargap=0.22, bargroupgap=0.06)
    fig.update_xaxes(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK)
    fig.update_yaxes(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK,
                     ticksuffix='%', row=1, col=1)
    fig.update_yaxes(gridcolor=_GRID_DARK, zerolinecolor=_GRID_DARK,
                     ticksuffix='%', row=2, col=1)
    return _div(fig)


def _chart_opt_heatmap(df_opt):
    """最佳化參數熱圖 (累積報酬率 & Alpha勝出年數)。"""
    periods = sorted(df_opt['para_period'].unique())
    crts    = sorted(df_opt['para_crt_chg'].unique(), reverse=True)

    z_ret, z_alpha = [], []
    for crt in crts:
        rr, ra = [], []
        for p in periods:
            m = df_opt[
                (df_opt['para_period'] == p) &
                (np.abs(df_opt['para_crt_chg'] - crt) < 1e-9)
            ]
            if not m.empty:
                rr.append(round((float(m['ret'].iloc[0]) - 1) * 100, 2))
                ra.append(int(m['alpha_year'].iloc[0]))
            else:
                rr.append(None); ra.append(None)
        z_ret.append(rr); z_alpha.append(ra)

    yl = [f"{c:.4f}" for c in crts]
    xl = [str(int(p)) for p in periods]

    fig = make_subplots(rows=1, cols=2, horizontal_spacing=0.14,
                        subplot_titles=['累積報酬率 (%)', 'Alpha 勝出年數'])
    fig.add_trace(go.Heatmap(z=z_ret, x=xl, y=yl, colorscale='RdYlGn',
                             text=[[f"{v:.1f}" if v is not None else '' for v in r] for r in z_ret],
                             texttemplate="%{text}",
                             textfont=dict(color='#0e1117'),
                             colorbar=dict(x=0.44, len=0.9, title='%',
                                           tickfont=dict(color=_TEXT_DARK))), row=1, col=1)
    fig.add_trace(go.Heatmap(z=z_alpha, x=xl, y=yl, colorscale='Blues',
                             text=[[str(v) if v is not None else '' for v in r] for r in z_alpha],
                             texttemplate="%{text}",
                             textfont=dict(color='#0e1117'),
                             colorbar=dict(x=1.01, len=0.9, title='年',
                                           tickfont=dict(color=_TEXT_DARK))), row=1, col=2)
    fig.update_layout(**_LAYOUT, height=440,
                      xaxis_title='回測期間 (天)',  xaxis2_title='回測期間 (天)',
                      yaxis_title='換股門檻 (crt_chg)')
    return _div(fig)


def _chart_opt_scatter(df_opt):
    """最佳化結果散點圖 (x=累積報酬率, y=Alpha年數)。"""
    ret_pct = ((df_opt['ret'] - 1) * 100).tolist()
    fig = go.Figure(go.Scatter(
        x=ret_pct,
        y=df_opt['alpha_year'].astype(int).tolist(),
        mode='markers',
        marker=dict(size=10, color=ret_pct,
                    colorscale='RdYlGn', showscale=True,
                    colorbar=dict(title='累積報酬%', len=0.8)),
        text=[f"Period={int(r['para_period'])}, Crt={r['para_crt_chg']:.4f}"
              for _, r in df_opt.iterrows()],
        hovertemplate='%{text}<br>報酬率: %{x:.2f}%<br>Alpha年數: %{y}<extra></extra>'
    ))
    fig.update_layout(**_LAYOUT, height=350,
                      xaxis_title='累積報酬率 (%)', yaxis_title='Alpha 勝出年數')
    return _div(fig)


# ═══════════════════════════════════════════════════════════════════════════════
# §4  CSS / JS / HTML 骨架
# ═══════════════════════════════════════════════════════════════════════════════

_CSS = """
:root{
  --bg:#0e1117;
  --panel:#1a2030;
  --panel-2:#222a3d;
  --border:#2c3344;
  --text:#e6edf3;
  --text-mute:#9aa4b2;
  --accent:#4ea1ff;
  --accent-2:#7cc5ff;
  --accent-soft:rgba(78,161,255,.12);
  --bench:#ff9f43;
  --pos:#22c55e;
  --neg:#ef4444;
}
*{box-sizing:border-box;}
html,body{background:var(--bg);}
body{font-family:'Segoe UI','Microsoft JhengHei',Arial,sans-serif;color:var(--text);}
a{color:var(--accent);}
::-webkit-scrollbar{width:9px;height:9px;}
::-webkit-scrollbar-track{background:var(--bg);}
::-webkit-scrollbar-thumb{background:#3a4358;border-radius:5px;}
::-webkit-scrollbar-thumb:hover{background:#4a5470;}

/* ── Header ─────────────────────────────────── */
.rpt-title{font-size:1.7rem;font-weight:800;color:var(--accent-2);letter-spacing:.3px;}
.rpt-sub{font-size:.85rem;color:var(--text-mute);margin-top:4px;}

/* ── KPI Cards ───────────────────────────────── */
.kpi-row{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:22px;}
.kpi-card{flex:1 1 165px;min-width:155px;background:var(--panel);border-radius:12px;
          padding:15px 14px 14px;box-shadow:0 2px 10px rgba(0,0,0,.45);text-align:center;
          border-top:3px solid var(--accent);border-left:1px solid var(--border);
          border-right:1px solid var(--border);border-bottom:1px solid var(--border);}
.kpi-label{font-size:.7rem;font-weight:700;color:var(--text-mute);text-transform:uppercase;
           letter-spacing:.7px;margin-bottom:6px;}
.kpi-val{font-size:1.5rem;font-weight:800;line-height:1.15;color:var(--text);}
.kpi-bm{font-size:.95rem;font-weight:700;color:var(--bench);margin-top:8px;
        padding-top:7px;border-top:1px dashed var(--border);}
.kpi-bm-lbl{display:block;font-size:.62rem;font-weight:700;color:var(--text-mute);
            letter-spacing:.6px;margin-bottom:2px;}

/* ── Colors ──────────────────────────────────── */
.pos{color:var(--pos)!important;}
.neg{color:var(--neg)!important;}

/* ── Tab navigation ──────────────────────────── */
.nav-tabs{border-bottom:2px solid var(--border);flex-wrap:nowrap;overflow-x:auto;}
.nav-tabs .nav-link{font-size:.86rem;font-weight:600;color:var(--text-mute);border:none;
                    padding:10px 16px;border-radius:0;white-space:nowrap;background:transparent;}
.nav-tabs .nav-link.active{color:var(--accent-2);border-bottom:3px solid var(--accent);
                            background:transparent;}
.nav-tabs .nav-link:hover{color:var(--accent-2);background:var(--accent-soft);}
.tab-content{background:var(--panel);border:1px solid var(--border);border-top:none;
             border-radius:0 0 10px 10px;padding:18px 18px 22px;}

/* ── Section cards ───────────────────────────── */
.sec-card{background:var(--panel-2);border-radius:10px;border:1px solid var(--border);
          box-shadow:0 1px 8px rgba(0,0,0,.35);padding:16px;margin-bottom:14px;}
.sec-title{font-size:.92rem;font-weight:700;color:var(--accent-2);
           margin-bottom:12px;padding-bottom:8px;
           border-bottom:2px solid var(--accent-soft);}

/* ── Metric table (績效 vs S&P 500) ───────── */
.mtbl{width:100%;font-size:.88rem;border-collapse:collapse;color:var(--text);}
.mtbl thead th{background:#222a3d;color:var(--accent-2);font-weight:700;
               padding:10px 16px;text-align:left;border-bottom:2px solid var(--accent);}
.mtbl tbody tr:nth-child(even){background:rgba(255,255,255,.02);}
.mtbl tbody tr:hover{background:var(--accent-soft);}
.mtbl td{padding:9px 16px;border-bottom:1px solid var(--border);vertical-align:middle;}
.mtbl td.lbl{font-weight:600;color:var(--text-mute);width:200px;}
.mtbl td.vp{font-weight:800;font-size:1rem;color:var(--text);}
.mtbl td.vb{font-weight:700;font-size:.96rem;color:var(--bench);}

/* ── Data tables ──────────────────────────────── */
.tbl{width:100%;border-collapse:collapse;font-size:.82rem;color:var(--text);}
.tbl thead th{background:#1d2638;color:var(--accent-2);padding:9px 10px;
              text-align:center;white-space:nowrap;border-bottom:2px solid var(--accent);
              position:sticky;top:0;z-index:2;cursor:pointer;user-select:none;}
.tbl thead th:hover{background:#27314a;color:#fff;}
.tbl tbody tr:nth-child(even){background:rgba(255,255,255,.02);}
.tbl tbody tr:hover{background:var(--accent-soft);}
.tbl tbody td{padding:6px 10px;border-bottom:1px solid var(--border);
              text-align:right;white-space:nowrap;}
.tbl tbody td:first-child{text-align:left;font-weight:600;color:var(--accent-2);}
.tbl-wrap{max-height:520px;overflow-y:auto;border-radius:8px;
          border:1px solid var(--border);box-shadow:0 1px 6px rgba(0,0,0,.4);}

/* ── Search box ──────────────────────────────── */
.srch{max-width:260px;font-size:.82rem !important;background:var(--panel) !important;
      color:var(--text) !important;border:1px solid var(--border) !important;}
.srch::placeholder{color:var(--text-mute);}

/* ── Badges ──────────────────────────────────── */
.bw{background:rgba(34,197,94,.18);color:#86efac;border-radius:99px;padding:2px 9px;font-size:.78rem;font-weight:700;}
.bl{background:rgba(239,68,68,.18);color:#fca5a5;border-radius:99px;padding:2px 9px;font-size:.78rem;font-weight:700;}
.bh{background:rgba(78,161,255,.2);color:#93c5fd;border-radius:99px;padding:2px 9px;font-size:.78rem;font-weight:700;}
.bq{background:rgba(168,85,247,.2);color:#d8b4fe;border-radius:99px;padding:2px 9px;font-size:.78rem;font-weight:700;}

/* ── Summary strip ───────────────────────────── */
.sum-strip{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
.sum-chip{background:var(--panel);border:1px solid var(--border);border-radius:8px;
          padding:7px 14px;font-size:.82rem;color:var(--text);}
.sum-chip b{color:var(--accent-2);}

/* ── Opt best card ───────────────────────────── */
.best-card{background:linear-gradient(135deg,#1e3a8a 0%,#3b82f6 100%);
           border-radius:14px;padding:22px 24px;color:#fff;margin-bottom:16px;
           box-shadow:0 4px 18px rgba(59,130,246,.35);}
.best-row{display:flex;flex-wrap:wrap;gap:24px;}
.best-item{text-align:center;}
.best-item .bc-val{font-size:1.7rem;font-weight:800;line-height:1.1;}
.best-item .bc-lbl{font-size:.74rem;opacity:.88;margin-top:4px;}

/* ── Settings ────────────────────────────────── */
.stbl{color:var(--text);}
.stbl td{padding:9px 16px;border-bottom:1px solid var(--border);font-size:.9rem;}
.stbl td:first-child{color:var(--text-mute);font-weight:600;width:220px;}

/* ── Annual table alpha column ───────────────── */
.alpha-pos{color:var(--pos);font-weight:700;}
.alpha-neg{color:var(--neg);font-weight:700;}

/* ── Today rebalance ─────────────────────────── */
.tr-banner{display:flex;align-items:center;gap:18px;background:var(--panel);
           border:1px solid var(--border);border-left:5px solid var(--accent);
           border-radius:12px;padding:18px 22px;margin-bottom:18px;}
.tr-banner.swap{border-left-color:var(--bench);}
.tr-banner.hold{border-left-color:var(--pos);}
.tr-icon{font-size:2.2rem;line-height:1;}
.tr-info .tr-date{font-size:.78rem;color:var(--text-mute);letter-spacing:.6px;}
.tr-info .tr-msg{font-size:1.3rem;font-weight:800;color:var(--text);margin-top:3px;}
.tr-info .tr-msg em{color:var(--accent-2);font-style:normal;}
.tr-cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:14px;}
.tr-card{flex:1 1 200px;background:var(--panel);border:1px solid var(--border);
         border-radius:10px;padding:12px 16px;}
.tr-card .tc-lbl{font-size:.72rem;color:var(--text-mute);font-weight:700;
                 letter-spacing:.5px;text-transform:uppercase;}
.tr-card .tc-val{font-size:1.4rem;font-weight:800;color:var(--text);margin-top:4px;}
.tr-card.buy{border-top:3px solid var(--pos);}
.tr-card.sell{border-top:3px solid var(--neg);}
.tr-card.hold{border-top:3px solid var(--accent);}
"""

_JS = r"""
function filterTable(sid, tid) {
    var q = document.getElementById(sid).value.toLowerCase();
    document.querySelectorAll('#' + tid + ' tbody tr').forEach(function(r) {
        r.style.display = r.innerText.toLowerCase().indexOf(q) >= 0 ? '' : 'none';
    });
}
function sortTable(th, tid) {
    var tbl  = document.getElementById(tid);
    var idx  = th.cellIndex;
    var asc  = th.dataset.asc !== 'true';
    th.dataset.asc = asc;
    var rows = Array.from(tbl.querySelectorAll('tbody tr'));
    rows.sort(function(a, b) {
        var av = a.cells[idx].innerText.replace(/[%,]/g,'').trim();
        var bv = b.cells[idx].innerText.replace(/[%,]/g,'').trim();
        var an = parseFloat(av), bn = parseFloat(bv);
        if (!isNaN(an) && !isNaN(bn)) return asc ? an - bn : bn - an;
        return asc ? av.localeCompare(bv, 'zh') : bv.localeCompare(av, 'zh');
    });
    tbl.querySelectorAll('thead th').forEach(function(h) {
        h.textContent = h.textContent.replace(/ [▲▼]$/, '');
    });
    rows.forEach(function(r) { tbl.querySelector('tbody').appendChild(r); });
    th.textContent += (asc ? ' ▲' : ' ▼');
}
"""

def _html_head(title):
    return (
        '<!DOCTYPE html>\n<html lang="zh-TW">\n<head>\n'
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'<title>{title}</title>\n'
        '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">\n'
        '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>\n'
        f'<style>{_CSS}</style>\n'
        '</head>\n<body>\n<div class="container-fluid px-4 py-3">\n'
    )

def _html_tail():
    return (
        '</div>\n'
        '<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>\n'
        f'<script>{_JS}</script>\n'
        '</body>\n</html>'
    )

def _tabs_html(tab_list):
    """
    tab_list: [(tid, label, html_content), ...]
    """
    nav = '<ul class="nav nav-tabs mt-3 mb-0" role="tablist">\n'
    panes = '<div class="tab-content">\n'
    for i, (tid, label, content) in enumerate(tab_list):
        active = ' active' if i == 0 else ''
        show   = ' show active' if i == 0 else ''
        nav += (
            f'  <li class="nav-item" role="presentation">'
            f'<button class="nav-link{active}" id="{tid}-tab" data-bs-toggle="tab" '
            f'data-bs-target="#{tid}" type="button" role="tab">{label}</button>'
            f'</li>\n'
        )
        panes += (
            f'<div class="tab-pane fade{show}" id="{tid}" role="tabpanel">\n'
            f'{content}\n</div>\n'
        )
    nav   += '</ul>\n'
    panes += '</div>\n'
    return nav + panes


def _tbl(df, tid, sid=None, fmt=None, cls_fn=None, badge=None):
    """
    DataFrame → 互動式 HTML 表格。
    fmt     = {col: fn(v) → str}
    cls_fn  = {col: fn(v) → css_class}
    badge   = {col: {val_str: badge_class}}
    """
    fmt    = fmt    or {}
    cls_fn = cls_fn or {}
    badge  = badge  or {}

    def render(c, v):
        try: na = pd.isna(v)
        except Exception: na = False
        if na: return '─'
        if c in fmt: return fmt[c](v)
        if hasattr(v, 'strftime'): return v.strftime('%Y-%m-%d')
        return str(v)

    def get_cls(c, v):
        bmap = badge.get(c)
        if bmap:
            return bmap.get(str(v), '')
        fn = cls_fn.get(c)
        if fn: return fn(v)
        return ''

    srch = ''
    if sid:
        srch = (
            f'<div class="mb-2">'
            f'<input class="form-control form-control-sm srch" id="{sid}" '
            f'type="text" placeholder="搜尋 ..." '
            f'oninput="filterTable(\'{sid}\',\'{tid}\')">'
            f'</div>\n'
        )

    thead = ''.join(
        f'<th onclick="sortTable(this,\'{tid}\')">{c}</th>'
        for c in df.columns
    )

    tbody = ''
    for _, row in df.iterrows():
        cells = ''
        for c in df.columns:
            v   = row[c]
            txt = render(c, v)
            css = get_cls(c, v)
            if css:
                cells += f'<td><span class="{css}">{txt}</span></td>'
            else:
                cells += f'<td>{txt}</td>'
        tbody += f'<tr>{cells}</tr>\n'

    return (
        f'{srch}'
        f'<div class="tbl-wrap">'
        f'<table class="tbl" id="{tid}">'
        f'<thead><tr>{thead}</tr></thead>'
        f'<tbody>{tbody}</tbody>'
        f'</table></div>'
    )


# ═══════════════════════════════════════════════════════════════════════════════
# §5  HTML 區塊產生器
# ═══════════════════════════════════════════════════════════════════════════════

def _kpi_html(pp, pb, avg_alpha, total_p, total_b):
    """KPI Cards (上方彙整指標)。"""
    stp = _sg(pp.get('stp_loss', 0))
    items = [
        ('年化報酬率',   _p(pp['ret']),     _p(pb['ret']),     _cls(pp['ret'])),
        ('累積報酬率',   _p(total_p),       _p(total_b),       _cls(total_p)),
        ('Sharpe Ratio', _n(pp['r/v']),    _n(pb['r/v']),    _cls(pp['r/v'])),
        ('最大回撤 MDD', _p(pp['MDD']),     _p(pb['MDD']),     _cls(pp['MDD'], good_pos=False)),
        ('年化波動度',   _p(pp['vol']),     _p(pb['vol']),     ''),
        ('年均 Alpha',   _p(avg_alpha),    '─',              _cls(avg_alpha)),
        ('年化換手率',   _p(pp['turn_over']),'─',             ''),
        ('停損次數',     str(int(stp)) if not np.isnan(stp) else '─', '─', ''),
    ]
    cards = ''
    for label, vp, vb, css in items:
        cards += (
            f'<div class="kpi-card">'
            f'  <div class="kpi-label">{label}</div>'
            f'  <div class="kpi-val {css}">{vp}</div>'
            f'  <div class="kpi-bm"><span class="kpi-bm-lbl">S&amp;P 500</span>{vb}</div>'
            f'</div>\n'
        )
    return f'<div class="kpi-row">{cards}</div>\n'


def _metrics_tbl(pp, pb, avg_alpha, total_p, total_b):
    """績效指標對照表。"""
    stp = _sg(pp.get('stp_loss', 0))
    rows_data = [
        ('年化報酬率',    _p(pp['ret']),           _p(pb['ret'])),
        ('累積報酬率',    _p(total_p),             _p(total_b)),
        ('年化波動度',    _p(pp['vol']),            _p(pb['vol'])),
        ('Sharpe Ratio', _n(pp['r/v']),           _n(pb['r/v'])),
        ('最大報酬率',    _p(pp['max_ret']),        _p(pb['max_ret'])),
        ('最小報酬率',    _p(pp['min_ret']),        _p(pb['min_ret'])),
        ('最大回撤 MDD', _p(pp['MDD']),            _p(pb['MDD'])),
        ('年均 Alpha',   _p(avg_alpha),            '─'),
        ('年化換手率',   _p(pp['turn_over']),       '─'),
        ('停損次數',     str(int(stp)) if not np.isnan(stp) else '─', '─'),
    ]
    rows_html = ''
    for lbl, vp, vb in rows_data:
        rows_html += f'<tr><td class="lbl">{lbl}</td><td class="vp">{vp}</td><td class="vb">{vb}</td></tr>\n'
    return (
        '<div class="sec-card">'
        '<div class="sec-title">績效指標對照表</div>'
        '<table class="mtbl">'
        '<thead><tr><th>指標</th><th>策略</th><th>S&amp;P 500</th></tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table></div>\n'
    )


def _annual_tbl(py):
    """逐年績效表。"""
    df = py[['p_ret', 'b_ret', 'alpha', 'p_ret_cul', 'b_ret_cul']].copy()
    df.index = [y.year for y in df.index]
    df.index.name = '年度'
    df = df.reset_index()
    df.columns = ['年度', '策略年報酬率', 'S&P 500年報酬率', 'Alpha', '策略累積報酬率', 'S&P 500累積報酬率']

    def pct(v):
        try:
            return '─' if pd.isna(v) else f"{v*100:.2f}%"
        except Exception: return '─'

    def alpha_cls(v):
        try:
            return 'alpha-pos' if float(v) > 0 else ('alpha-neg' if float(v) < 0 else '')
        except Exception: return ''

    return (
        '<div class="sec-card">'
        '<div class="sec-title">逐年績效明細</div>' +
        _tbl(df, 'tbl_annual',
             fmt={
                 '策略年報酬率': pct, 'S&P 500年報酬率': pct,
                 'Alpha': pct,
                 '策略累積報酬率': lambda v: pct(v - 1) if not pd.isna(v) else '─',
                 'S&P 500累積報酬率': lambda v: pct(v - 1) if not pd.isna(v) else '─',
             },
             cls_fn={
                 '策略年報酬率': lambda v: _cls(v),
                 'S&P 500年報酬率': lambda v: _cls(v),
                 'Alpha': alpha_cls,
             }) +
        '</div>\n'
    )


def _daily_perf_tbl(perf):
    """每日績效表。"""
    df = perf[['p_ret', 'b_ret', 'p_ret_cul', 'b_ret_cul',
               'mv', 'cost', 'unreal_pl', 'real_pl', 'tlt_pl']].copy()
    df.index.name = '日期'
    df = df.reset_index()
    df.columns = ['日期', '策略日報酬', 'S&P 500日報酬', '策略累積報酬', 'S&P 500累積報酬',
                  '市值', '成本', '未實現損益', '已實現損益', '總損益']
    df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')

    def pct(v):
        return '─' if pd.isna(v) else f"{v*100:.3f}%"

    def cul_pct(v):
        return '─' if pd.isna(v) else f"{(v-1)*100:.2f}%"

    return (
        '<div class="sec-card">'
        '<div class="sec-title">每日投資組合績效</div>' +
        _tbl(df, 'tbl_daily', sid='srch_daily',
             fmt={
                 '策略日報酬': pct, 'S&P 500日報酬': pct,
                 '策略累積報酬': cul_pct, 'S&P 500累積報酬': cul_pct,
                 '市值': _a, '成本': _a, '未實現損益': _a, '已實現損益': _a, '總損益': _a,
             },
             cls_fn={
                 '策略日報酬': lambda v: _cls(v),
                 'S&P 500日報酬': lambda v: _cls(v),
                 '未實現損益': lambda v: _cls(v),
                 '已實現損益': lambda v: _cls(v),
                 '總損益': lambda v: _cls(v),
             }) +
        '</div>\n'
    )


def _holdings_tbl(trade_log):
    """持倉明細表：每筆進出場紀錄。"""
    if trade_log is None or trade_log.empty:
        return '<div class="sec-card"><div class="sec-title">持倉明細</div><p class="text-muted small">無持倉資料。</p></div>'

    df = trade_log[[
        'Ticker', '入場日', '出場日', '持有天數',
        '入場金額', '出場金額', '損益', '報酬率', '勝負'
    ]].copy()

    badge_map = {'勝負': {'勝': 'bw', '敗': 'bl', '持有中': 'bh', '平手': 'bq'}}

    return (
        '<div class="sec-card">'
        '<div class="sec-title">持倉明細（進出場紀錄）</div>' +
        _tbl(df, 'tbl_hold', sid='srch_hold',
             fmt={
                 '入場日': _dt, '出場日': _dt,
                 '入場金額': _a, '出場金額': _a, '損益': _a,
                 '報酬率': lambda v: _p(v) if pd.notna(v) else '─',
             },
             cls_fn={
                 '損益': lambda v: _cls(v),
                 '報酬率': lambda v: _cls(v),
             },
             badge=badge_map) +
        '</div>\n'
    )


def _trade_tbl(trade_log, trade):
    """換股紀錄：摘要統計 + 原始買賣明細。"""
    summ = _trade_summary(trade_log)
    strip = ''
    if summ:
        items = [
            ('總交易次數', str(summ.get('總交易次數', '─'))),
            ('勝出次數',   str(summ.get('勝出次數', '─'))),
            ('虧損次數',   str(summ.get('虧損次數', '─'))),
            ('勝率',       _p(summ.get('勝率', np.nan))),
            ('平均持有天數', str(summ.get('平均持有天數', '─'))),
            ('最大持有天數', str(summ.get('最大持有天數', '─'))),
            ('平均獲利率', _p(summ.get('平均獲利率', np.nan))),
            ('平均虧損率', _p(summ.get('平均虧損率', np.nan))),
        ]
        strip = '<div class="sum-strip">' + ''.join(
            f'<div class="sum-chip">{k}: <b>{v}</b></div>' for k, v in items
        ) + '</div>\n'

    tr = _prep_trade(trade)
    if tr.empty:
        detail = '<p class="text-muted small">無交易資料。</p>'
    else:
        df = tr[['date', 'stk_bbg', 't_shares', 't_amt']].copy()
        df['type'] = df['t_shares'].apply(lambda x: '買進' if x > 0 else '賣出')
        df = df.sort_values('date', ascending=False)
        df.columns = ['日期', 'Ticker', '股數', '金額', '方向']
        df = df[['日期', 'Ticker', '方向', '股數', '金額']]
        detail = _tbl(df, 'tbl_trade', sid='srch_trade',
                      fmt={
                          '日期': _dt,
                          '股數': lambda v: _a(v, 0),
                          '金額': _a,
                      },
                      badge={'方向': {'買進': 'bw', '賣出': 'bl'}})

    return (
        '<div class="sec-card">'
        '<div class="sec-title">交易統計摘要</div>'
        + strip + '</div>\n'
        '<div class="sec-card">'
        '<div class="sec-title">買賣交易明細</div>'
        + detail + '</div>\n'
    )


def _stock_prof_tbl(sp):
    """個股分析表：勝率、買賣次數、持有天數、Slope 訊號。"""
    if sp is None or sp.empty:
        return '<div class="sec-card"><div class="sec-title">個股分析</div><p class="text-muted small">無資料。</p></div>'

    df = sp.copy()
    df['勝率'] = df['勝率'].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else '─')

    badge_map = {'最新訊號': {'多頭': 'bw', '空頭': 'bl', '─': 'bq'}}

    return (
        '<div class="sec-card">'
        '<div class="sec-title">個股統計分析</div>' +
        _tbl(df, 'tbl_stock', sid='srch_stock',
             fmt={
                 '最新Slope分數': lambda v: _n(v, 6) if pd.notna(v) else '─',
                 '平均持有天數': lambda v: _n(v, 1) if pd.notna(v) else '─',
             },
             badge=badge_map) +
        '</div>\n'
    )


def _latest_signal_tab(signal_data, hold_portfolio):
    """最新訊號 Tab：顯示最後一個交易日的全部股票池訊號 & Slope 分數。

    股票池來源優先序：
      1. signal_data (含所有 universe 個股)
      2. hold_portfolio (僅含持倉)
    """
    src = None
    if signal_data is not None and len(signal_data) > 0:
        src = signal_data.copy()
    elif hold_portfolio is not None:
        hp = _prep_hp(hold_portfolio)
        if not hp.empty:
            src = hp.copy()

    if src is None or src.empty or 'date' not in src.columns:
        return ('<div class="sec-card"><div class="sec-title">最新訊號</div>'
                '<p class="text-muted small">無訊號資料。</p></div>')

    last_date = src['date'].max()
    df = src[src['date'] == last_date].copy()

    # 取出可用欄位
    cols_keep = [c for c in ['stk_bbg', 'signal', 'indicator'] if c in df.columns]
    df = df[cols_keep].copy()
    df = df.dropna(subset=['indicator']) if 'indicator' in df.columns else df
    if 'indicator' in df.columns:
        df = df.sort_values('indicator', ascending=False)

    # 計算排名
    if 'indicator' in df.columns:
        df.insert(0, '排名', range(1, len(df) + 1))

    # 標記是否入選
    hp = _prep_hp(hold_portfolio)
    if not hp.empty:
        hold_set = set(hp[hp['date'] == hp['date'].max()]['stk_bbg'].tolist())
    else:
        hold_set = set()
    df['是否持倉'] = df['stk_bbg'].apply(lambda s: '✔' if s in hold_set else '')

    # 訊號文字
    def _sig_lbl(v):
        try:
            f = float(v)
            if f > 0: return '多頭'
            if f < 0: return '空頭'
        except Exception: pass
        return '─'
    if 'signal' in df.columns:
        df['訊號'] = df['signal'].apply(_sig_lbl)

    # 重命名與重排列
    rename_map = {
        'stk_bbg':   'Ticker',
        'indicator': 'Slope分數',
        'signal':    'signal_raw',
    }
    df = df.rename(columns=rename_map)
    if 'signal_raw' in df.columns: df = df.drop(columns=['signal_raw'])

    order = [c for c in ['排名', 'Ticker', '訊號', 'Slope分數', '是否持倉'] if c in df.columns]
    df = df[order].reset_index(drop=True)

    # 統計 Header
    pos_n = (df['訊號'] == '多頭').sum() if '訊號' in df.columns else 0
    neg_n = (df['訊號'] == '空頭').sum() if '訊號' in df.columns else 0
    hold_n = (df['是否持倉'] == '✔').sum()

    chips = (
        '<div class="sum-strip">'
        f'<div class="sum-chip">最新日: <b>{_dt(last_date)}</b></div>'
        f'<div class="sum-chip">股票池數: <b>{len(df)}</b></div>'
        f'<div class="sum-chip">多頭訊號: <b>{pos_n}</b></div>'
        f'<div class="sum-chip">空頭訊號: <b>{neg_n}</b></div>'
        f'<div class="sum-chip">現持倉: <b>{hold_n}</b></div>'
        '</div>\n'
    )

    badge_map = {
        '訊號': {'多頭': 'bw', '空頭': 'bl', '─': 'bq'},
        '是否持倉': {'✔': 'bh', '': ''},
    }
    table = _tbl(
        df, 'tbl_latest_sig', sid='srch_latest_sig',
        fmt={'Slope分數': lambda v: _n(v, 6) if pd.notna(v) else '─'},
        badge=badge_map,
    )

    return (
        '<div class="sec-card">'
        '<div class="sec-title">最新一日股票池訊號 & Slope 分數</div>'
        + chips + table +
        '</div>\n'
    )


def _today_rebalance_tab(hold_portfolio, trade):
    """
    今日換股：比較最後一個交易日 vs 前一個交易日的持倉差異。

    顯示：
      - 是否換股 Banner
      - 統計卡片（買進 / 賣出 / 續抱檔數）
      - 買進 / 賣出 / 續抱明細表（含 Slope 分數、訊號）
    """
    hp = _prep_hp(hold_portfolio)
    tr = _prep_trade(trade)

    if hp.empty:
        return ('<div class="tr-banner"><div class="tr-icon">⚠️</div>'
                '<div class="tr-info"><div class="tr-msg">無持倉資料</div></div></div>')

    dates = sorted(hp['date'].unique())
    last_date = dates[-1]
    prev_date = dates[-2] if len(dates) >= 2 else None

    today_df = hp[hp['date'] == last_date].copy()
    prev_df  = hp[hp['date'] == prev_date].copy() if prev_date is not None else pd.DataFrame()

    today_set = set(today_df['stk_bbg'].tolist())
    prev_set  = set(prev_df['stk_bbg'].tolist()) if not prev_df.empty else set()

    buy_set  = today_set - prev_set
    sell_set = prev_set - today_set
    keep_set = today_set & prev_set

    swap = bool(buy_set or sell_set)

    # ── Banner ──────────────────────────────────────────────
    if swap:
        icon = '🔄'
        msg  = (f'今日 <em>換股</em>：'
                f'買進 {len(buy_set)} 檔、賣出 {len(sell_set)} 檔、'
                f'續抱 {len(keep_set)} 檔')
        cls  = 'swap'
    else:
        icon = '✅'
        msg  = f'今日 <em>不換股</em>，續抱 {len(keep_set)} 檔'
        cls  = 'hold'

    banner = (
        f'<div class="tr-banner {cls}">'
        f'  <div class="tr-icon">{icon}</div>'
        f'  <div class="tr-info">'
        f'    <div class="tr-date">最近交易日：{_dt(last_date)}'
        + (f'　｜　比較基準日：{_dt(prev_date)}' if prev_date is not None else '')
        + f'</div>'
        f'    <div class="tr-msg">{msg}</div>'
        f'  </div>'
        f'</div>\n'
    )

    cards = (
        '<div class="tr-cards">'
        f'  <div class="tr-card buy">'
        f'    <div class="tc-lbl">今日買進</div>'
        f'    <div class="tc-val">{len(buy_set)} 檔</div></div>'
        f'  <div class="tr-card sell">'
        f'    <div class="tc-lbl">今日賣出</div>'
        f'    <div class="tc-val">{len(sell_set)} 檔</div></div>'
        f'  <div class="tr-card hold">'
        f'    <div class="tc-lbl">續抱</div>'
        f'    <div class="tc-val">{len(keep_set)} 檔</div></div>'
        f'  <div class="tr-card">'
        f'    <div class="tc-lbl">當日總持倉</div>'
        f'    <div class="tc-val">{len(today_set)} 檔</div></div>'
        '</div>\n'
    )

    # ── 動作明細表 ──────────────────────────────────────────
    rows = []
    for stk in sorted(buy_set):
        row    = today_df[today_df['stk_bbg'] == stk].iloc[0]
        ind    = row.get('indicator', np.nan)
        sig    = row.get('signal', np.nan)
        tr_row = tr[(tr['date'] == last_date) & (tr['stk_bbg'] == stk)]
        amt    = float(tr_row['t_amt'].iloc[0]) if not tr_row.empty else np.nan
        rows.append(dict(動作='買進', Ticker=stk, Slope分數=ind,
                         訊號=('多頭' if pd.notna(sig) and sig > 0 else
                              '空頭' if pd.notna(sig) and sig < 0 else '─'),
                         交易金額=amt))
    for stk in sorted(sell_set):
        ind    = np.nan
        if not prev_df.empty:
            r = prev_df[prev_df['stk_bbg'] == stk]
            if not r.empty: ind = r.iloc[0].get('indicator', np.nan)
        tr_row = tr[(tr['date'] == last_date) & (tr['stk_bbg'] == stk)]
        amt    = abs(float(tr_row['t_amt'].iloc[0])) if not tr_row.empty else np.nan
        rows.append(dict(動作='賣出', Ticker=stk, Slope分數=ind,
                         訊號='出場', 交易金額=amt))
    for stk in sorted(keep_set):
        row = today_df[today_df['stk_bbg'] == stk].iloc[0]
        ind = row.get('indicator', np.nan)
        sig = row.get('signal', np.nan)
        rows.append(dict(動作='續抱', Ticker=stk, Slope分數=ind,
                         訊號=('多頭' if pd.notna(sig) and sig > 0 else
                              '空頭' if pd.notna(sig) and sig < 0 else '─'),
                         交易金額=np.nan))

    if rows:
        df = pd.DataFrame(rows)
        badge_map = {
            '動作': {'買進': 'bw', '賣出': 'bl', '續抱': 'bh'},
            '訊號': {'多頭': 'bw', '空頭': 'bl', '出場': 'bq', '─': 'bq'},
        }
        detail = _tbl(
            df, 'tbl_today',
            fmt={
                'Slope分數': lambda v: _n(v, 6) if pd.notna(v) else '─',
                '交易金額':  lambda v: _a(v) if pd.notna(v) else '─',
            },
            badge=badge_map,
        )
    else:
        detail = '<p class="text-muted small">無持倉。</p>'

    return (
        banner +
        cards +
        '<div class="sec-card">'
        '<div class="sec-title">換股 / 持倉明細</div>'
        + detail +
        '</div>\n'
    )


def _settings_html(meta):
    """回測設定資訊表。"""
    rows = [
        ('標的指數',    meta.get('index', '─')),
        ('持股數',      str(meta.get('stk_num', '─'))),
        ('初始資金',    _a(meta.get('ini_amt', np.nan))),
        ('停損設定',    _p(meta.get('stop_loss', np.nan))),
        ('回測期間',    f"{meta.get('start_date','─')} ～ {meta.get('end_date','─')}"),
        ('Slope 期間', str(meta.get('period', meta.get('best_period', '─'))) + ' 天'),
        ('換股門檻',    _n(meta.get('crt_chg', meta.get('best_crt_chg', np.nan)), 4)),
    ]
    rows_html = ''.join(f'<tr class="srow"><td>{k}</td><td>{v}</td></tr>' for k, v in rows)
    return (
        '<div class="sec-card">'
        '<div class="sec-title">回測參數設定</div>'
        f'<table class="stbl"><tbody>{rows_html}</tbody></table>'
        '</div>\n'
    )


# ═══════════════════════════════════════════════════════════════════════════════
# §6  公開介面：單次回測報表
# ═══════════════════════════════════════════════════════════════════════════════

def build_backtest_report(performance, performance_year, performance_tlt,
                          hold_portfolio, trade, holdings,
                          meta, signal_data=None,
                          output_path='slope_backtest_report.html',
                          auto_open=True):
    """
    產生單次回測互動式 HTML 報表。

    Parameters
    ----------
    performance      : 每日績效 DataFrame (index=date)
    performance_year : 逐年績效 DataFrame
    performance_tlt  : 總體績效 DataFrame (index=['p','b'])
    hold_portfolio   : 每日持倉 DataFrame
    trade            : 交易紀錄 DataFrame (index=date)
    holdings         : 最終持倉狀態 DataFrame (index=stk_bbg)
    meta             : dict 含 index, stk_num, ini_amt, stop_loss,
                            period, crt_chg, start_date, end_date
    output_path      : 輸出 HTML 路徑
    auto_open        : 是否自動以瀏覽器開啟
    """
    # ── 衍生資料 ──────────────────────────────────────────────────────────────
    trade_log  = _compute_trade_log(hold_portfolio, trade)
    stock_prof = _compute_stock_profile(trade_log, hold_portfolio, trade)

    pp = performance_tlt.loc['p']
    pb = performance_tlt.loc['b']
    avg_alpha = performance_year['alpha'].mean()
    total_p   = _sg(performance['p_ret_cul'].iloc[-1]) - 1
    total_b   = _sg(performance['b_ret_cul'].iloc[-1]) - 1

    # ── 圖表 ──────────────────────────────────────────────────────────────────
    c_cum = _chart_cum_ret(performance)
    c_ann = _chart_annual(performance_year)

    # ── Tab 內容 ──────────────────────────────────────────────────────────────

    # Tab 0: 今日換股
    t0 = _today_rebalance_tab(hold_portfolio, trade)

    # Tab 1: 績效總覽
    t1 = (
        _kpi_html(pp, pb, avg_alpha, total_p, total_b) +
        _metrics_tbl(pp, pb, avg_alpha, total_p, total_b)
    )

    # Tab 2: 累積報酬率
    t2 = (
        '<div class="sec-card"><div class="sec-title">策略 vs S&amp;P 500：累積報酬率 &amp; 回撤</div>'
        + c_cum + '</div>\n'
    )

    # Tab 3: 逐年績效（圖在上、表在下）
    t3 = (
        '<div class="sec-card"><div class="sec-title">逐年報酬率比較 & 每年 Alpha</div>'
        + c_ann + '</div>\n'
        + _annual_tbl(performance_year)
    )

    # Tab 4: 每日績效
    t4 = _daily_perf_tbl(performance)

    # Tab 5: 持倉明細
    t5 = _holdings_tbl(trade_log)

    # Tab 6: 換股紀錄
    t6 = _trade_tbl(trade_log, trade)

    # Tab 7: 最新訊號（股票池）
    t7 = _latest_signal_tab(signal_data, hold_portfolio)

    # Tab 8: 回測設定
    t8 = _settings_html(meta)

    # ── 組合頁面 ──────────────────────────────────────────────────────────────
    tab_list = [
        ('t8', '回測設定',   t8),
        ('t1', '績效總覽',   t1),
        ('t2', '累積報酬率', t2),
        ('t3', '逐年績效',   t3),
        ('t4', '每日績效',   t4),
        ('t5', '持倉明細',   t5),
        ('t6', '換股紀錄',   t6),
        ('t7', '最新訊號',   t7),
        ('t0', '今日換股',   t0),
    ]

    start = meta.get('start_date', '─')
    end   = meta.get('end_date',   '─')
    idx   = meta.get('index',      '─')
    header = (
        f'<div class="mb-3">'
        f'<div class="rpt-title">Slope 策略回測報表</div>'
        f'<div class="rpt-sub">'
        f'回測區間：{start} ～ {end}　｜　'
        f'標的指數：{idx}　｜　'
        f'持股數：{meta.get("stk_num","─")}　｜　'
        f'Slope 期間：{meta.get("period","─")} 天　｜　'
        f'換股門檻：{_n(meta.get("crt_chg", np.nan), 4)}'
        f'</div></div>\n'
    )

    html = (
        _html_head('Slope 策略回測報表') +
        header +
        _tabs_html(tab_list) +
        _html_tail()
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    if auto_open:
        webbrowser.open('file:///' + os.path.abspath(output_path).replace('\\', '/'))

    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
# §7  公開介面：最佳化回測報表
# ═══════════════════════════════════════════════════════════════════════════════

def _opt_overview_tab(df_opt, meta):
    """最佳化總覽：最佳參數 Banner + 熱圖 + 散點圖。"""
    best_period  = meta.get('best_period',  '─')
    best_crt_chg = meta.get('best_crt_chg', np.nan)
    # best row stats
    mask = (
        (df_opt['para_period'] == best_period) &
        (np.abs(df_opt['para_crt_chg'] - best_crt_chg) < 1e-9)
    )
    best = df_opt[mask].iloc[0] if mask.any() else df_opt.iloc[0]
    best_ret   = float(best['ret'])
    best_alpha = int(best['alpha_year'])

    banner = (
        '<div class="best-card">'
        '<div style="font-size:.95rem;font-weight:700;margin-bottom:14px;opacity:.92;">最佳參數組合</div>'
        '<div class="best-row">'
        f'<div class="best-item"><div class="bc-val">{int(best_period)}</div><div class="bc-lbl">Slope 期間 (天)</div></div>'
        f'<div class="best-item"><div class="bc-val">{_n(best_crt_chg,4)}</div><div class="bc-lbl">換股門檻</div></div>'
        f'<div class="best-item"><div class="bc-val">{_p(best_ret - 1)}</div><div class="bc-lbl">累積報酬率</div></div>'
        f'<div class="best-item"><div class="bc-val">{best_alpha}</div><div class="bc-lbl">Alpha 勝出年數</div></div>'
        f'<div class="best-item"><div class="bc-val">{df_opt.shape[0]}</div><div class="bc-lbl">總組合數</div></div>'
        '</div></div>\n'
    )

    heatmap_div = _chart_opt_heatmap(df_opt)
    scatter_div = _chart_opt_scatter(df_opt)

    return (
        banner +
        '<div class="sec-card"><div class="sec-title">參數熱圖</div>' + heatmap_div + '</div>\n' +
        '<div class="sec-card"><div class="sec-title">散點分布圖（各組合累積報酬率 vs Alpha年數）</div>' + scatter_div + '</div>\n'
    )


def _opt_detail_tab(df_opt):
    """最佳化詳細結果表格。"""
    df = df_opt.copy().sort_values(['alpha_year', 'ret'], ascending=[False, False])
    df = df.reset_index(drop=True)
    df.columns = ['Slope期間', '換股門檻', '累積報酬率', 'Alpha勝出年數']
    return (
        '<div class="sec-card">'
        '<div class="sec-title">所有參數組合結果</div>' +
        _tbl(df, 'tbl_opt', sid='srch_opt',
             fmt={
                 '換股門檻':  lambda v: _n(v, 4),
                 '累積報酬率': lambda v: _p(v - 1),
                 'Alpha勝出年數': lambda v: str(int(v)),
             },
             cls_fn={
                 '累積報酬率': lambda v: _cls(v - 1),
             }) +
        '</div>\n'
    )


def build_opt_report(df_opt,
                 meta,
                 output_path='slope_opt_report.html',
                 auto_open=True):
    """
    產生最佳化回測互動式 HTML 報表。

    Parameters
    ----------
    df_opt           : 最佳化結果 DataFrame (para_period, para_crt_chg, ret, alpha_year)
    meta             : dict 含 index, stk_num, ini_amt, stop_loss,
                            best_period, best_crt_chg, start_date, end_date
    output_path      : 輸出 HTML 路徑
    auto_open        : 是否自動以瀏覽器開啟
    """
    # Tab 1: 最佳化總覽
    t_opt = _opt_overview_tab(df_opt, meta)

    # Tab 2: 參數明細
    t_detail = _opt_detail_tab(df_opt)

    # Tab 8: 回測設定
    # 組合 meta 加入 best 參數欄位（供 settings 顯示）
    smeta = dict(meta)
    smeta.setdefault('period',  meta.get('best_period',  '─'))
    smeta.setdefault('crt_chg', meta.get('best_crt_chg', np.nan))
    t_settings = _settings_html(smeta)

    # ── 組合頁面 ──────────────────────────────────────────────────────────────
    tab_list = [
        ('to8', '回測設定',        t_settings),
        ('to1', '最佳化總覽',      t_opt),
        ('to2', '參數比較',        t_detail),
    ]

    start = meta.get('start_date', '─')
    end   = meta.get('end_date',   '─')
    idx   = meta.get('index',      '─')
    bp    = meta.get('best_period',  '─')
    bc    = meta.get('best_crt_chg', np.nan)
    header = (
        f'<div class="mb-3">'
        f'<div class="rpt-title">Slope 策略最佳化報表</div>'
        f'<div class="rpt-sub">'
        f'回測區間：{start} ～ {end}　｜　'
        f'標的指數：{idx}　｜　'
        f'持股數：{meta.get("stk_num","─")}　｜　'
        f'最佳 Slope 期間：{bp} 天　｜　'
        f'最佳換股門檻：{_n(bc,4)}'
        f'</div></div>\n'
    )

    html = (
        _html_head('Slope 策略最佳化報表') +
        header +
        _tabs_html(tab_list) +
        _html_tail()
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    if auto_open:
        webbrowser.open('file:///' + os.path.abspath(output_path).replace('\\', '/'))

    return output_path
