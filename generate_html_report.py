# -*- coding: utf-8 -*-
"""
產生 HTML 績效分析報告 (tab UI, 每個圖表獨立分頁)
"""
import sys, io
from pathlib import Path
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from portfolio_analysis import load_data, FP, SECTOR_EN_MAP

OUTPUT_HTML = Path(__file__).parent / '績效分析報告.html'

# ===== Colors =====
COLOR_PORT = '#1F4E79'
COLOR_BENCH = '#A6A6A6'
COLOR_POS = '#4CAF50'
COLOR_NEG = '#E53935'
COLOR_ACCENT = '#F39C12'
COLOR_INFO = '#3498DB'
COLOR_PURPLE = '#9B59B6'
PLOTLY_TEMPLATE = 'plotly_white'
CHART_FONT = dict(family='Microsoft JhengHei, Arial, sans-serif', size=12)


# ===== Formatting helpers =====
def fmt_pct(x, digits=2, sign=True):
    if x is None or pd.isna(x):
        return 'n/a'
    return f'{x*100:+.{digits}f}%' if sign else f'{x*100:.{digits}f}%'

def fmt_usd(x, digits=0):
    if x is None or pd.isna(x):
        return 'n/a'
    return f'${x:,.{digits}f}'

def fmt_usd_m(x, digits=1):
    if x is None or pd.isna(x):
        return 'n/a'
    return f'${x/1e6:,.{digits}f}M'


def fig_to_div(fig, div_id):
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id=div_id, config={'displaylogo': False})


def df_to_html_table(df, classes='data-table'):
    return df.to_html(classes=classes, index=False, escape=False, border=0)


def _convert_brinson_to_legacy(brinson_df):
    """
    把自算 Brinson sector_df 轉成 Bloomberg-style 欄位名稱 (值 ×100 → %).

    輸入欄位 (自算 Brinson):
        sector, w_p, w_b, w_active, r_p, r_b, r_active,
        allocation, selection, interaction, total
    輸出新增欄位 (Bloomberg-style, % 形式):
        name, wt_port, wt_bench, wt_active,
        tr_port, tr_bench, tr_active,
        industry_active (= allocation),
        sel_active     (= selection),
        interact_active (= interaction),
        ctr_active     (= total)
    """
    if 'allocation' not in brinson_df.columns:
        return brinson_df  # 已經是 legacy 格式
    out = brinson_df.copy()
    out['name'] = out['sector']
    out['wt_port'] = out['w_p'] * 100
    out['wt_bench'] = out['w_b'] * 100
    out['wt_active'] = out['w_active'] * 100
    out['tr_port'] = out['r_p'].fillna(0) * 100
    out['tr_bench'] = out['r_b'].fillna(0) * 100
    out['tr_active'] = out['r_active'].fillna(0) * 100
    out['industry_active'] = out['allocation'] * 100
    out['sel_active'] = out['selection'] * 100
    if 'interaction' in out.columns:
        out['interact_active'] = out['interaction'] * 100
    out['ctr_active'] = out['total'] * 100
    return out


def _fmt_attr_cell(v, digits):
    if pd.isna(v):
        return '<span style="color:#bbb">n/a</span>'
    if v == 0:
        return '0'
    return f'{v:+.{digits}f}'


def _build_expandable_attribution_table(sector_df, bb_securities, held_only=False, table_id='attr-table'):
    """
    建可展開的歸因表:
      - 第一層: sector 列, 可點擊
      - 第二層: 該 sector 下的個股, 預設隱藏
      - held_only=True: 第二層只顯示組合有持有的個股 (wt_port > 0)
    """
    cols = [
        ('wt_port', 'wP%', 2),
        ('wt_bench', 'wB%', 2),
        ('wt_active', 'wActive%', 2),
        ('tr_port', 'rP%', 2),
        ('tr_bench', 'rB%', 2),
        ('tr_active', 'rActive%', 2),
        ('industry_active', 'Allocation', 3),
        ('sel_active', 'Selection', 3),
        ('interact_active', 'Interaction', 3),
        ('ctr_active', 'CTR', 3),
    ]
    cols = [c for c in cols if c[0] in sector_df.columns]

    def _safe_cell(row, col, digits):
        if col not in row.index:
            return '<span style="color:#bbb">-</span>'
        return _fmt_attr_cell(row[col], digits)
    sd = sector_df.copy().sort_values('ctr_active', ascending=False).reset_index(drop=True)

    html = [f'<table class="data-table expandable-table" id="{table_id}">']
    html.append('<thead><tr>')
    html.append('<th>Sector / 證券</th>')
    for _, hdr, _ in cols:
        html.append(f'<th>{hdr}</th>')
    html.append('</tr></thead>')
    html.append('<tbody>')

    for _, srow in sd.iterrows():
        sector_name = str(srow['name'])
        sid = sector_name.replace(' ', '_')  # 用作 data-attribute id
        # Sector 列
        html.append(f'<tr class="sector-row" data-sector="{sid}">')
        html.append(f'<td><span class="toggle">▶</span> <strong>{sector_name}</strong></td>')
        for col, _, digits in cols:
            html.append(f'<td>{_safe_cell(srow, col, digits)}</td>')
        html.append('</tr>')

        # 該 sector 下的證券, 按 |ctr_active| 大到小排; held_only 時只留 wt_port>0
        sec_in_sec = bb_securities[bb_securities['sector'] == sector_name].copy()
        if held_only:
            sec_in_sec = sec_in_sec[sec_in_sec['wt_port'].notna() & (sec_in_sec['wt_port'] > 0)]
        sec_in_sec['_abs_ctr'] = sec_in_sec['ctr_active'].abs()
        sec_in_sec = sec_in_sec.sort_values('_abs_ctr', ascending=False, na_position='last')
        for _, secr in sec_in_sec.iterrows():
            html.append(f'<tr class="sec-detail" data-parent="{sid}" style="display:none">')
            sec_name = str(secr['name'])
            html.append(f'<td style="padding-left:36px; color:#5D6D7E">└ {sec_name}</td>')
            for col, _, digits in cols:
                html.append(f'<td>{_safe_cell(secr, col, digits)}</td>')
            html.append('</tr>')

    html.append('</tbody></table>')
    return '\n'.join(html)


# =============================================================
# Chart builders
# =============================================================
def chart_return_bar(perf):
    labels = ['簡單法', 'Modified Dietz', '組合報酬 (自算 TWRR)', 'SPY (基準)', 'Active Return']
    values = [
        (perf.get('simple_return') or 0) * 100,
        (perf.get('md_return') or 0) * 100,
        perf['port_return'] * 100,
        perf['spy_return'] * 100,
        perf['active_return'] * 100,
    ]
    colors = [COLOR_BENCH, COLOR_INFO, COLOR_PORT, COLOR_BENCH,
              COLOR_POS if perf['active_return'] > 0 else COLOR_NEG]
    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=colors,
        text=[f'{v:+.2f}%' for v in values], textposition='outside',
        cliponaxis=False,
        hovertemplate='%{x}: %{y:+.2f}%<extra></extra>',
    ))
    y_max = max(values)
    y_min = min(0, min(values))
    pad = (y_max - y_min) * 0.18
    fig.update_layout(
        title='期間報酬率比較 (組合各口徑 vs SPY)',
        yaxis_title='報酬率', yaxis_ticksuffix='%',
        yaxis_range=[y_min - pad, y_max + pad],
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=440,
        showlegend=False, margin=dict(t=60, b=40, l=60, r=40),
    )
    return fig


def chart_daily_mv(daily, perf):
    df = daily.copy()
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['DATE_'], y=df['mv']/1e6, name='市值 MV',
        line=dict(color=COLOR_PORT, width=2.5),
        hovertemplate='%{x|%Y-%m-%d}<br>MV: $%{y:,.1f}M<extra></extra>',
    ))
    fig.add_trace(go.Scatter(
        x=df['DATE_'], y=df['cost']/1e6, name='庫存成本 Cost',
        line=dict(color=COLOR_ACCENT, width=2, dash='dot'),
        hovertemplate='%{x|%Y-%m-%d}<br>Cost: $%{y:,.1f}M<extra></extra>',
    ))
    fig.update_layout(
        title=f'每日市值 vs 庫存成本 ({df["DATE_"].min().date()} ~ {df["DATE_"].max().date()})',
        xaxis_title='日期', yaxis_title='USD 百萬',
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=460,
        hovermode='x unified',
        margin=dict(t=60, b=40, l=60, r=40),
    )
    return fig


def chart_daily_pnl(daily):
    df = daily.copy()
    fig = make_subplots(specs=[[{'secondary_y': True}]])
    fig.add_trace(go.Scatter(
        x=df['DATE_'], y=df['pnl']/1e6, name='累積總損益',
        line=dict(color=COLOR_PORT, width=2.5),
        fill='tozeroy', fillcolor='rgba(31, 78, 121, 0.1)',
        hovertemplate='%{x|%Y-%m-%d}<br>P&L: $%{y:+,.1f}M<extra></extra>',
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=df['DATE_'], y=df['urcg']/1e6, name='未實現',
        line=dict(color=COLOR_INFO, width=1.5, dash='dash'),
        hovertemplate='%{x|%Y-%m-%d}<br>URCG: $%{y:+,.1f}M<extra></extra>',
    ), secondary_y=False)
    fig.add_trace(go.Scatter(
        x=df['DATE_'], y=df['n_holdings'], name='持倉檔數',
        line=dict(color=COLOR_PURPLE, width=1.5, dash='dot'),
        hovertemplate='%{x|%Y-%m-%d}<br>持倉: %{y}<extra></extra>',
    ), secondary_y=True)
    fig.update_layout(
        title='每日累積 P&L / 持倉檔數',
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=460,
        hovermode='x unified',
        margin=dict(t=60, b=40, l=60, r=60),
    )
    fig.update_yaxes(title_text='USD 百萬', secondary_y=False)
    fig.update_yaxes(title_text='持倉檔數', secondary_y=True)
    fig.update_xaxes(title_text='日期')
    return fig


def chart_pnl_decomp(pnl_d):
    labels = ['URCG (未實現)', 'RCG (已實現)', 'DVD (股息)']
    values = [pnl_d['urcg'], pnl_d['rcg'], pnl_d['dvd']]
    colors = [COLOR_POS if v >= 0 else COLOR_NEG for v in values]
    fig = go.Figure(go.Bar(
        x=labels, y=[v/1e6 for v in values], marker_color=colors,
        text=[f'${v/1e6:+,.1f}M' for v in values], textposition='outside',
        cliponaxis=False,
        hovertemplate='%{x}: $%{y:+,.2f}M<extra></extra>',
    ))
    y_max = max(v/1e6 for v in values)
    y_min = min(0, min(v/1e6 for v in values))
    pad = (y_max - y_min) * 0.15
    fig.update_layout(
        title='P&L 結構分解',
        yaxis_title='金額 (USD 百萬)',
        yaxis_range=[y_min - pad, y_max + pad],
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=440,
        showlegend=False, margin=dict(t=60, b=40, l=60, r=40),
    )
    return fig


def chart_contributors(all_contrib, quota_usd):
    """全部曾持有 ticker 對額度報酬的貢獻 (P&L / 額度)."""
    df = all_contrib.sort_values('TOTAL_PL', ascending=True).copy()
    df['ctr_pct'] = df['TOTAL_PL'] / quota_usd * 100
    colors = [COLOR_POS if v > 0 else COLOR_NEG for v in df['ctr_pct']]
    labels = df['ticker'].str.split().str[0] + ' ' + df['STK_NAME'].fillna('').astype(str).str[:12]
    text_str = [f'{c:+.2f}% (${p/1e6:+,.1f}M)' for c, p in zip(df['ctr_pct'], df['TOTAL_PL'])]
    fig = go.Figure(go.Bar(
        y=labels, x=df['ctr_pct'],
        orientation='h',
        marker_color=colors,
        text=text_str,
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>CTR: %{x:+.3f}%<br>P&L: $%{customdata:+,.2f}M<extra></extra>',
        customdata=df['TOTAL_PL']/1e6,
    ))
    x_max = df['ctr_pct'].max()
    x_min = df['ctr_pct'].min()
    pad = max(abs(x_max), abs(x_min)) * 0.3
    fig.update_layout(
        title=f'各持股對組合報酬貢獻 (P&L / 額度 ${quota_usd/1e6:.0f}M, 含已平倉部位)',
        xaxis_title='報酬率貢獻 (%)',
        xaxis_ticksuffix='%',
        xaxis_range=[x_min - pad, x_max + pad],
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=max(620, 24 * len(df) + 100),
        showlegend=False, margin=dict(t=60, b=40, l=200, r=120),
    )
    return fig


def chart_holdings_weights(held_sorted):
    """左圖: 組合權重 + SPY 權重 grouped horizontal bars, 按組合權重降冪."""
    df = held_sorted.copy().reset_index(drop=True)
    df = df.iloc[::-1]
    labels = (df['ticker'].str.split().str[0] + ' ' +
              df['STK_NAME'].fillna('').astype(str).str[:8])
    w_bench = df.get('wt_bench', pd.Series([0]*len(df), index=df.index)).fillna(0).astype(float)
    port_w = df['weight'] * 100
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=labels, x=port_w, orientation='h',
        marker_color=COLOR_PORT, name='組合',
        text=[f'{v:.2f}%' for v in port_w],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>組合: %{x:.2f}%<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=labels, x=w_bench, orientation='h',
        marker_color=COLOR_BENCH, name='SPY',
        text=[f'{v:.2f}%' if v > 0 else '—' for v in w_bench],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>SPY: %{x:.2f}%<extra></extra>',
    ))
    fig.update_layout(
        title='組合 vs SPY 權重',
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=max(720, 32 * len(df) + 140),
        barmode='group', bargap=0.15, bargroupgap=0.04,
        margin=dict(t=70, b=40, l=140, r=40),
        xaxis=dict(title='權重 (%)', ticksuffix='%', zeroline=True),
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.01, xanchor='center', x=0.5),
    )
    return fig


def chart_holdings_pnl_pct(held_sorted):
    """右圖: P&L% YTD horizontal bar, 與權重圖按相同順序 (組合權重降冪)."""
    df = held_sorted.copy().reset_index(drop=True)
    df['pnl_pct'] = df['TOTAL_PL'] / df['TOTAL_COST'].replace(0, np.nan)
    df = df.iloc[::-1]
    labels = (df['ticker'].str.split().str[0] + ' ' +
              df['STK_NAME'].fillna('').astype(str).str[:8])
    pnl_pct = df['pnl_pct'].fillna(0) * 100
    colors = [COLOR_POS if v > 0 else (COLOR_NEG if v < 0 else '#BDBDBD') for v in pnl_pct]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=labels, x=pnl_pct, orientation='h',
        marker_color=colors, name='P&L% YTD',
        text=[f'{v:+.1f}%' for v in pnl_pct],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>P&L% (YTD): %{x:+.2f}%<br>P&L: $%{customdata:,.0f}<extra></extra>',
        customdata=df['TOTAL_PL'],
    ))
    pmax = max(pnl_pct.max(), 1)
    pmin = min(pnl_pct.min(), 0)
    pad = max(abs(pmax), abs(pmin), 1) * 0.25
    fig.update_layout(
        title='P&L% (YTD)',
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=max(720, 32 * len(df) + 140),
        bargap=0.20,
        margin=dict(t=70, b=40, l=140, r=80),
        xaxis=dict(title='P&L% (YTD)', ticksuffix='%',
                    range=[pmin - pad, pmax + pad],
                    zeroline=True, zerolinecolor='#444', zerolinewidth=1.2),
        showlegend=False,
    )
    return fig


def _with_wt_bench(held_sorted, bb_secs):
    """Merge Bloomberg wt_bench + tr_bench (SPY 權重/報酬, 已是 %) into held_sorted by ticker."""
    df = held_sorted.copy()
    if bb_secs is None or 'code' not in bb_secs.columns:
        df['wt_bench'] = 0
        df['tr_bench'] = np.nan
        return df
    tk = bb_secs.copy()
    tk['_code'] = tk['code'].apply(lambda x: str(x).split()[0].upper().strip() if pd.notna(x) else None)
    lookup_wb = tk.dropna(subset=['_code']).groupby('_code')['wt_bench'].max()
    lookup_tr = tk.dropna(subset=['_code']).groupby('_code')['tr_bench'].max() if 'tr_bench' in tk.columns else None
    df['_code'] = df['ticker'].str.split().str[0].str.upper().str.strip()
    df['wt_bench'] = df['_code'].map(lookup_wb).fillna(0)
    if lookup_tr is not None:
        df['tr_bench'] = df['_code'].map(lookup_tr)
    else:
        df['tr_bench'] = np.nan
    df = df.drop(columns=['_code'])
    return df


def _alpha_stats(held_w_bench, spy_extra):
    """Compute trend slope + Pearson ρ for Active Weight × P&L% over held + SPY 母體."""
    df = held_w_bench.copy()
    df['_pnl_pct'] = df['TOTAL_PL'] / df['TOTAL_COST'].replace(0, np.nan)
    port_w = df['weight'] * 100
    w_bench = df.get('wt_bench', pd.Series([0]*len(df), index=df.index)).fillna(0).astype(float)
    aw_held = (port_w - w_bench).reset_index(drop=True)
    pnl_held = (df['_pnl_pct'].fillna(0) * 100).reset_index(drop=True)
    all_x = list(aw_held.values)
    all_y = list(pnl_held.values)
    if spy_extra is not None and len(spy_extra) > 0:
        sp = spy_extra.dropna(subset=['tr_bench'])
        all_x.extend((-sp['wt_bench'].fillna(0)).values)
        all_y.extend(sp['tr_bench'].values)
    if len(all_x) < 2:
        return None, None, 0
    x_arr = np.array(all_x); y_arr = np.array(all_y)
    slope, _ = np.polyfit(x_arr, y_arr, 1)
    corr = float(np.corrcoef(x_arr, y_arr)[0, 1])
    return float(slope), corr, len(all_x)


def _with_held_at_start(held_df, h_start):
    """Mark each ticker whether held at period start (期初持有 vs 期內新進)."""
    df = held_df.copy()
    h_start_tickers = set(h_start[h_start['TOTAL_MV'] > 0]['ticker']) if h_start is not None else set()
    df['_held_at_start'] = df['ticker'].apply(lambda t: t in h_start_tickers)
    return df


def _spy_universe(bb_secs, held_codes):
    """SPY 成分股 (排除已持有的 ticker) — 給 chart_top_holdings 當背景用."""
    if bb_secs is None or 'code' not in bb_secs.columns:
        return pd.DataFrame()
    tk = bb_secs.copy()
    tk['_code'] = tk['code'].apply(lambda x: str(x).split()[0].upper().strip() if pd.notna(x) else None)
    # 排除已持有的 + 只保留 wt_bench > 0
    in_spy = tk[tk['_code'].notna() & (tk['wt_bench'].fillna(0) > 0) & (~tk['_code'].isin(held_codes))]
    return in_spy[['_code', 'name', 'wt_bench', 'tr_bench']].copy()


def chart_top_holdings(held_sorted, spy_extra=None):
    """Quadrant Scatter: Active Weight (x) vs P&L% YTD (y)
       前景: 持有部位 (大圓, 顏色按象限, 大小=組合 MV, 顯 ticker)
       背景: SPY 母體未持有 (小灰圓, 顯示 missed/avoided 對照)
       加 trend line (跨全 SPY+持有) 看相關性
    """
    df = held_sorted.copy().reset_index(drop=True)
    df['pnl_pct'] = df['TOTAL_PL'] / df['TOTAL_COST'].replace(0, np.nan)

    w_bench = df.get('wt_bench', pd.Series([0]*len(df), index=df.index)).fillna(0).astype(float)
    port_w = df['weight'] * 100
    active_w = port_w - w_bench
    pnl_pct = df['pnl_pct'].fillna(0) * 100
    mv = df['TOTAL_MV'].fillna(0)

    # 象限分類: Q1=超配+賺, Q2=低配+漲, Q3=低配+跌, Q4=超配+虧
    def quadrant(aw, p):
        if aw > 0 and p > 0: return 'Q1'
        if aw < 0 and p > 0: return 'Q2'
        if aw < 0 and p < 0: return 'Q3'
        if aw > 0 and p < 0: return 'Q4'
        return 'Q0'  # 零軸
    quads = [quadrant(a, p) for a, p in zip(active_w, pnl_pct)]
    QUAD_COLORS = {
        'Q1': '#2E7D32',   # 深綠 (alpha gen)
        'Q2': '#F9A825',   # 黃 (missed)
        'Q3': '#1565C0',   # 藍 (smart avoid)
        'Q4': '#C62828',   # 紅 (bad bet)
        'Q0': '#BDBDBD',
    }
    point_colors = [QUAD_COLORS[q] for q in quads]

    # 點大小: MV scaled
    mv_min, mv_max = mv.min(), mv.max()
    if mv_max > mv_min:
        sizes = 12 + (mv - mv_min) / (mv_max - mv_min) * 45
    else:
        sizes = pd.Series([20] * len(df))
    sizes = sizes.fillna(12)

    labels = df['ticker'].str.split().str[0]
    hover_company = df['STK_NAME'].fillna('').astype(str).str[:14]

    fig = go.Figure()

    # === 背景: SPY 母體未持有 (小灰圓) ===
    if spy_extra is not None and len(spy_extra) > 0:
        sp = spy_extra.copy()
        sp['_active_w'] = -sp['wt_bench'].fillna(0)  # port=0 → active = -wt_bench
        sp['_pnl_pct'] = sp['tr_bench']  # SPY 內部報酬 (已是 %)
        sp = sp.dropna(subset=['_pnl_pct'])
        if len(sp) > 0:
            # 點大小: SPY 權重 scaled (但很小)
            wb_max = sp['wt_bench'].max() or 1
            sp_sizes = 5 + (sp['wt_bench'] / wb_max) * 12
            # 顏色: 按象限 (Q2 / Q3)
            sp_colors = [QUAD_COLORS['Q2'] if p > 0 else (QUAD_COLORS['Q3'] if p < 0 else '#BDBDBD')
                         for p in sp['_pnl_pct']]
            fig.add_trace(go.Scatter(
                x=sp['_active_w'], y=sp['_pnl_pct'],
                mode='markers',
                marker=dict(size=sp_sizes, color=sp_colors, opacity=0.35,
                            line=dict(width=0)),
                name=f'SPY 未持有 ({len(sp)} 檔, 對照)',
                customdata=list(zip(sp['name'], sp['wt_bench'], sp['tr_bench'])),
                hovertemplate=(
                    '<b>%{customdata[0]}</b><br>'
                    'SPY 權重: %{customdata[1]:.2f}%<br>'
                    'SPY YTD: %{customdata[2]:+.2f}%<br>'
                    'Active Weight: %{x:+.2f}% (未持有)'
                    '<extra></extra>'
                ),
            ))

    # === 前景: 持有部位, 分 期初持有 vs 期內新進 兩個 trace ===
    held_at_start_flags = df.get('_held_at_start', pd.Series([False]*len(df), index=df.index))
    idx_start = held_at_start_flags[held_at_start_flags].index
    idx_new = held_at_start_flags[~held_at_start_flags].index

    def _add_held_trace(idx, symbol, name_prefix, marker_line_color):
        if len(idx) == 0:
            return
        fig.add_trace(go.Scatter(
            x=active_w.loc[idx], y=pnl_pct.loc[idx],
            mode='markers+text',
            marker=dict(
                size=[sizes.loc[i] for i in idx],
                color=[point_colors[i] for i in idx],
                symbol=symbol,
                opacity=0.88,
                line=dict(width=2.0, color=marker_line_color),
            ),
            text=[labels.iloc[i] for i in idx],
            textposition='top center', textfont=dict(size=10, color='#333'),
            name=f'{name_prefix} ({len(idx)} 檔)',
            customdata=[
                (hover_company.iloc[i], port_w.iloc[i], w_bench.iloc[i],
                 df['TOTAL_PL'].iloc[i], df['TOTAL_MV'].iloc[i])
                for i in idx
            ],
            hovertemplate=(
                '<b>%{text}</b> %{customdata[0]}  · ' + name_prefix + '<br>'
                'Active Weight: %{x:+.2f}%<br>'
                'P&L% (YTD): %{y:+.2f}%<br>'
                'Port: %{customdata[1]:.2f}% / SPY: %{customdata[2]:.2f}%<br>'
                'P&L: $%{customdata[3]:,.0f} · MV: $%{customdata[4]:,.0f}'
                '<extra></extra>'
            ),
        ))

    _add_held_trace(idx_start, 'circle', '期初持有 ●', 'white')
    _add_held_trace(idx_new, 'diamond', '期內新進 ◆', '#222')

    # Trend line: 跨所有點 (持有 + SPY 未持有)
    all_x = list(active_w.dropna().values)
    all_y = list(pnl_pct.dropna().values)
    if spy_extra is not None and len(spy_extra) > 0:
        sp = spy_extra.dropna(subset=['tr_bench'])
        all_x.extend((-sp['wt_bench'].fillna(0)).values)
        all_y.extend(sp['tr_bench'].values)
    if len(all_x) >= 2:
        x_arr = np.array(all_x); y_arr = np.array(all_y)
        slope, intercept = np.polyfit(x_arr, y_arr, 1)
        corr = np.corrcoef(x_arr, y_arr)[0, 1]
        x_line = np.linspace(x_arr.min(), x_arr.max(), 50)
        y_line = slope * x_line + intercept
        fig.add_trace(go.Scatter(
            x=x_line, y=y_line, mode='lines',
            line=dict(color='#5D6D7E', width=2, dash='dash'),
            name=f'Trend (all): slope={slope:.2f}, ρ={corr:+.3f}',
            hoverinfo='skip',
        ))

    aw_max = max(abs(active_w.min()), abs(active_w.max()), 1)
    aw_pad = aw_max * 0.20
    pnl_max_v = max(pnl_pct.max(), 0)
    pnl_min_v = min(pnl_pct.min(), 0)
    pnl_range = max(abs(pnl_max_v), abs(pnl_min_v), 1)
    pnl_pad = pnl_range * 0.20

    x_lo, x_hi = -aw_max - aw_pad, aw_max + aw_pad
    y_lo, y_hi = pnl_min_v - pnl_pad, pnl_max_v + pnl_pad

    # 象限背景色帶 (淡色 shapes)
    quad_shapes = [
        # Q1: 右上
        dict(type='rect', xref='x', yref='y', x0=0, x1=x_hi, y0=0, y1=y_hi,
             fillcolor='rgba(46,125,50,0.06)', line=dict(width=0), layer='below'),
        # Q2: 左上
        dict(type='rect', xref='x', yref='y', x0=x_lo, x1=0, y0=0, y1=y_hi,
             fillcolor='rgba(249,168,37,0.06)', line=dict(width=0), layer='below'),
        # Q3: 左下
        dict(type='rect', xref='x', yref='y', x0=x_lo, x1=0, y0=y_lo, y1=0,
             fillcolor='rgba(21,101,192,0.06)', line=dict(width=0), layer='below'),
        # Q4: 右下
        dict(type='rect', xref='x', yref='y', x0=0, x1=x_hi, y0=y_lo, y1=0,
             fillcolor='rgba(198,40,40,0.06)', line=dict(width=0), layer='below'),
    ]

    # 象限標籤 (corner annotations)
    quad_annots = [
        dict(x=x_hi*0.95, y=y_hi*0.92, xref='x', yref='y',
             text=f'<b>Q1 · 超配+賺</b><br><span style="color:#666">Alpha 來源</span>',
             showarrow=False, font=dict(size=11, color='#2E7D32'),
             align='right', bgcolor='rgba(255,255,255,0.7)'),
        dict(x=x_lo*0.95, y=y_hi*0.92, xref='x', yref='y',
             text=f'<b>Q2 · 低配+漲</b><br><span style="color:#666">錯過</span>',
             showarrow=False, font=dict(size=11, color='#F9A825'),
             align='left', bgcolor='rgba(255,255,255,0.7)'),
        dict(x=x_lo*0.95, y=y_lo*0.92, xref='x', yref='y',
             text=f'<b>Q3 · 低配+跌</b><br><span style="color:#666">避開 (好)</span>',
             showarrow=False, font=dict(size=11, color='#1565C0'),
             align='left', bgcolor='rgba(255,255,255,0.7)'),
        dict(x=x_hi*0.95, y=y_lo*0.92, xref='x', yref='y',
             text=f'<b>Q4 · 超配+虧</b><br><span style="color:#666">押錯</span>',
             showarrow=False, font=dict(size=11, color='#C62828'),
             align='right', bgcolor='rgba(255,255,255,0.7)'),
    ]

    fig.update_layout(
        title=f'Active Weight vs P&L% (YTD): 重押個股是否表現較好? ({len(df)} 檔, 點大小=MV)',
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=720,
        margin=dict(t=80, b=60, l=70, r=70),
        xaxis=dict(
            title='<b>Active Weight</b> (Port − SPY) %', ticksuffix='%',
            range=[x_lo, x_hi],
            zeroline=True, zerolinecolor='#444', zerolinewidth=1.5,
            showgrid=True, gridcolor='rgba(127,140,141,0.15)',
        ),
        yaxis=dict(
            title='<b>P&L% (YTD)</b>', ticksuffix='%',
            range=[y_lo, y_hi],
            zeroline=True, zerolinecolor='#444', zerolinewidth=1.5,
            showgrid=True, gridcolor='rgba(127,140,141,0.15)',
        ),
        shapes=quad_shapes,
        annotations=quad_annots,
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
    )
    return fig


def chart_sector_weight_return(sector_df):
    """整合產業權重曝險 + 報酬率: 2 panels, shared y-axis.
       左: 組合權重 vs SPY 權重 (grouped bars)
       右: 組合報酬率 vs SPY 報酬率 (grouped bars)
       共用 y 軸, 按 Active Weight 由大到小排序.
    """
    df = sector_df.copy().sort_values('wt_active', ascending=True)
    df['sector_label'] = df['name'].map(lambda x: f"{x} / {SECTOR_EN_MAP.get(x, '')}")

    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=['產業權重曝險: 組合 vs SPY (期末)', '產業報酬率: 組合 vs SPY'],
        horizontal_spacing=0.10, shared_yaxes=True,
    )

    # ===== 左: 權重 =====
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['wt_port'].fillna(0),
        orientation='h', name='組合', marker_color=COLOR_PORT,
        text=[f'{v:.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['wt_port'].fillna(0)],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>組合權重: %{x:.2f}%<extra></extra>',
        legendgroup='weights',
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['wt_bench'].fillna(0),
        orientation='h', name='SPY', marker_color=COLOR_BENCH, opacity=0.78,
        text=[f'{v:.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['wt_bench'].fillna(0)],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>SPY 權重: %{x:.2f}%<extra></extra>',
        legendgroup='weights',
    ), row=1, col=1)

    # ===== 右: 報酬率 =====
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['tr_port'].fillna(0),
        orientation='h', name='組合 rP', marker_color=COLOR_PORT, showlegend=False,
        text=[f'{v:+.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['tr_port'].fillna(0)],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>組合 rP: %{x:+.2f}%<extra></extra>',
        legendgroup='returns',
    ), row=1, col=2)
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['tr_bench'].fillna(0),
        orientation='h', name='SPY rB', marker_color=COLOR_BENCH, opacity=0.78, showlegend=False,
        text=[f'{v:+.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['tr_bench'].fillna(0)],
        textposition='outside', cliponaxis=False, textfont=dict(size=11),
        hovertemplate='%{y}<br>SPY rB: %{x:+.2f}%<extra></extra>',
        legendgroup='returns',
    ), row=1, col=2)

    max_w = df[['wt_port', 'wt_bench']].max().max()
    r_lo = df[['tr_port', 'tr_bench']].min().min()
    r_hi = df[['tr_port', 'tr_bench']].max().max()
    r_pad = max(abs(r_lo), abs(r_hi), 1) * 0.18

    fig.update_xaxes(title='權重 (%)', ticksuffix='%', row=1, col=1, range=[0, max_w * 1.20])
    fig.update_xaxes(title='報酬率 (%)', ticksuffix='%', row=1, col=2,
                     range=[min(0, r_lo) - r_pad, r_hi + r_pad],
                     zeroline=True, zerolinecolor='#444', zerolinewidth=1.2)
    fig.update_layout(
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=max(560, 38 * len(df) + 120),
        barmode='group', bargap=0.18, bargroupgap=0.04,
        margin=dict(t=80, b=40, l=220, r=80),
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.04, xanchor='center', x=0.5),
    )
    return fig


def chart_sector_quadrant(sector_df):
    """Sector 象限 bubble: Active Weight (x) vs Active Return r_active (y).
       點大小 = |CTR_active|; 顏色按 4 象限.
    """
    df = sector_df.copy()
    df = df.dropna(subset=['wt_active', 'tr_active'])
    if len(df) == 0:
        return go.Figure()

    wa = df['wt_active'].fillna(0).astype(float)
    ra = df['tr_active'].fillna(0).astype(float)
    if 'ctr_active' in df.columns:
        ctr_abs = df['ctr_active'].fillna(0).abs().astype(float)
    else:
        ctr_abs = (wa.abs() * ra.abs()) / 100

    def _quad(a, r):
        if a > 0 and r > 0: return 'Q1'
        if a < 0 and r > 0: return 'Q2'
        if a < 0 and r < 0: return 'Q3'
        if a > 0 and r < 0: return 'Q4'
        return 'Q0'
    quads = [_quad(a, r) for a, r in zip(wa, ra)]
    QC = {'Q1': '#2E7D32', 'Q2': '#F9A825', 'Q3': '#1565C0', 'Q4': '#C62828', 'Q0': '#BDBDBD'}
    point_colors = [QC[q] for q in quads]

    ctr_max = max(ctr_abs.max(), 0.01)
    sizes = 20 + (ctr_abs / ctr_max) * 60

    # Dynamic textposition: 上半圖 → 標籤往下; 下半圖 → 標籤往上; 右側 → 標籤往左; 左側 → 標籤往右
    # 避免標籤被剪掉或被象限 annotation 蓋住
    aw_mid_x = (wa.max() + wa.min()) / 2
    ra_mid_y = (ra.max() + ra.min()) / 2
    text_positions = []
    for a, r in zip(wa, ra):
        # 預設右上, 但是邊角要反向
        vpos = 'bottom' if r > ra_mid_y else 'top'
        hpos = 'left' if a > aw_mid_x else 'right'
        text_positions.append(f'{vpos} {hpos}')

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=wa, y=ra, mode='markers+text',
        marker=dict(size=sizes, color=point_colors, opacity=0.82,
                    line=dict(width=1.5, color='white')),
        text=df['name'],
        textposition=text_positions,
        textfont=dict(size=12, color='#333'),
        cliponaxis=False,
        name='Sectors',
        customdata=list(zip(df.get('tr_port', pd.Series([np.nan]*len(df))).fillna(0),
                            df.get('tr_bench', pd.Series([np.nan]*len(df))).fillna(0),
                            ctr_abs)),
        hovertemplate=(
            '<b>%{text}</b><br>'
            'Active Weight: %{x:+.2f}%<br>'
            'Active Return: %{y:+.2f}%<br>'
            '組合 rP: %{customdata[0]:+.2f}% / SPY rB: %{customdata[1]:+.2f}%<br>'
            '|CTR_active|: %{customdata[2]:.3f}%'
            '<extra></extra>'
        ),
    ))

    # trend line
    if len(df) >= 2:
        slope, intercept = np.polyfit(wa.values, ra.values, 1)
        corr = float(np.corrcoef(wa.values, ra.values)[0, 1])
        x_line = np.linspace(wa.min(), wa.max(), 50)
        y_line = slope * x_line + intercept
        fig.add_trace(go.Scatter(
            x=x_line, y=y_line, mode='lines',
            line=dict(color='#5D6D7E', width=2, dash='dash'),
            name=f'Trend: slope={slope:.2f}, ρ={corr:+.3f}',
            hoverinfo='skip',
        ))
        fig._sector_trend = (slope, corr, len(df))  # 暫存給 narrative 用
    else:
        fig._sector_trend = (None, None, len(df))

    aw_max = max(abs(wa.min()), abs(wa.max()), 1)
    aw_pad = aw_max * 0.35   # 額外 padding 避免 label 被切
    ra_max = max(ra.max(), 0)
    ra_min = min(ra.min(), 0)
    ra_range = max(abs(ra_max), abs(ra_min), 1)
    ra_pad = ra_range * 0.30
    x_lo, x_hi = -aw_max - aw_pad, aw_max + aw_pad
    y_lo, y_hi = ra_min - ra_pad, ra_max + ra_pad

    quad_shapes = [
        dict(type='rect', xref='x', yref='y', x0=0, x1=x_hi, y0=0, y1=y_hi,
             fillcolor='rgba(46,125,50,0.06)', line=dict(width=0), layer='below'),
        dict(type='rect', xref='x', yref='y', x0=x_lo, x1=0, y0=0, y1=y_hi,
             fillcolor='rgba(249,168,37,0.06)', line=dict(width=0), layer='below'),
        dict(type='rect', xref='x', yref='y', x0=x_lo, x1=0, y0=y_lo, y1=0,
             fillcolor='rgba(21,101,192,0.06)', line=dict(width=0), layer='below'),
        dict(type='rect', xref='x', yref='y', x0=0, x1=x_hi, y0=y_lo, y1=0,
             fillcolor='rgba(198,40,40,0.06)', line=dict(width=0), layer='below'),
    ]
    # 象限 annotation 放到 plot 角落 (paper 座標), 避免覆蓋 data point label
    quad_annots = [
        dict(x=0.99, y=0.99, xref='paper', yref='paper',
             text='<b>Q1 · 超配+超額</b>',
             showarrow=False, font=dict(size=11, color='#2E7D32'),
             align='right', bgcolor='rgba(255,255,255,0.75)',
             xanchor='right', yanchor='top'),
        dict(x=0.01, y=0.99, xref='paper', yref='paper',
             text='<b>Q2 · 低配+漲</b>',
             showarrow=False, font=dict(size=11, color='#F9A825'),
             align='left', bgcolor='rgba(255,255,255,0.75)',
             xanchor='left', yanchor='top'),
        dict(x=0.01, y=0.01, xref='paper', yref='paper',
             text='<b>Q3 · 低配+跌</b>',
             showarrow=False, font=dict(size=11, color='#1565C0'),
             align='left', bgcolor='rgba(255,255,255,0.75)',
             xanchor='left', yanchor='bottom'),
        dict(x=0.99, y=0.01, xref='paper', yref='paper',
             text='<b>Q4 · 超配+輸</b>',
             showarrow=False, font=dict(size=11, color='#C62828'),
             align='right', bgcolor='rgba(255,255,255,0.75)',
             xanchor='right', yanchor='bottom'),
    ]

    fig.update_layout(
        title=f'產業 Active Weight vs Active Return: 重押產業是否超額? ({len(df)} sectors, 點大小=|CTR|)',
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=620,
        margin=dict(t=80, b=60, l=70, r=70),
        xaxis=dict(
            title='<b>Active Weight</b> (Port − SPY) %', ticksuffix='%',
            range=[x_lo, x_hi],
            zeroline=True, zerolinecolor='#444', zerolinewidth=1.5,
            showgrid=True, gridcolor='rgba(127,140,141,0.15)',
        ),
        yaxis=dict(
            title='<b>Active Return</b> (rP − rB) %', ticksuffix='%',
            range=[y_lo, y_hi],
            zeroline=True, zerolinecolor='#444', zerolinewidth=1.5,
            showgrid=True, gridcolor='rgba(127,140,141,0.15)',
        ),
        shapes=quad_shapes,
        annotations=quad_annots,
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
    )
    return fig


def chart_sector_returns(sector_df):
    """產業期間報酬率: 組合 vs SPY"""
    df = sector_df.copy().sort_values('tr_active', ascending=True)
    df['sector_label'] = df['name'].map(lambda x: f"{x} / {SECTOR_EN_MAP.get(x, '')}")

    fig = make_subplots(rows=1, cols=2,
                        subplot_titles=['產業報酬率: 組合 vs SPY', '產業 Active Return (rP - rB)'],
                        horizontal_spacing=0.18)

    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['tr_port'].fillna(0),
        orientation='h', name='組合 rP', marker_color=COLOR_PORT,
        text=[f'{v:+.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['tr_port'].fillna(0)],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>組合 rP: %{x:+.2f}%<extra></extra>',
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['tr_bench'].fillna(0),
        orientation='h', name='SPY rB', marker_color=COLOR_BENCH, opacity=0.75,
        text=[f'{v:+.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['tr_bench'].fillna(0)],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>SPY rB: %{x:+.2f}%<extra></extra>',
    ), row=1, col=1)

    colors = [COLOR_POS if v > 0 else COLOR_NEG for v in df['tr_active'].fillna(0)]
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['tr_active'].fillna(0),
        orientation='h', marker_color=colors, showlegend=False,
        text=[f'{v:+.1f}%' for v in df['tr_active'].fillna(0)],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>rActive: %{x:+.2f}%<extra></extra>',
    ), row=1, col=2)

    r_lo = df[['tr_port', 'tr_bench']].min().min()
    r_hi = df[['tr_port', 'tr_bench']].max().max()
    r_pad = max(abs(r_lo), abs(r_hi)) * 0.18
    ra_lo = df['tr_active'].min()
    ra_hi = df['tr_active'].max()
    ra_pad = max(abs(ra_lo), abs(ra_hi)) * 0.22
    fig.update_xaxes(ticksuffix='%', row=1, col=1, range=[min(0, r_lo) - r_pad, r_hi + r_pad])
    fig.update_xaxes(ticksuffix='%', row=1, col=2, range=[ra_lo - ra_pad, ra_hi + ra_pad])
    fig.update_layout(
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=540,
        barmode='group', margin=dict(t=80, b=40, l=220, r=60),
    )
    return fig


def chart_attribution_waterfall(perf):
    # 7 根 bar: Benchmark / Portfolio / Active / Allocation / Selection / Interaction / Residual
    labels = ['Benchmark<br>(SPY)', 'Portfolio<br>(TWRR)', 'Active Return',
              '└─ Allocation', '└─ Selection', '└─ Interaction', '└─ Residual']
    values = [
        (perf['spy_return'] or 0) * 100,
        (perf['port_return'] or 0) * 100,
        (perf['active_return'] or 0) * 100,
        (perf['industry_active'] or 0) * 100,
        (perf['selection_active'] or 0) * 100,
        (perf.get('interaction_active') or 0) * 100,
        (perf.get('residual_active') or 0) * 100,
    ]
    colors = [COLOR_BENCH, COLOR_PORT, COLOR_PURPLE,
              COLOR_INFO, COLOR_ACCENT, COLOR_POS, '#95A5A6']

    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=colors,
        text=[f'{v:+.2f}%' for v in values], textposition='outside',
        cliponaxis=False,
        hovertemplate='%{x}: %{y:+.2f}%<extra></extra>',
    ))
    y_max = max(values)
    y_min = min(0, min(values))
    pad = (y_max - y_min) * 0.18
    fig.update_layout(
        title='Brinson Attribution: Portfolio = Benchmark + Active = Benchmark + Allocation + Selection + Interaction + Residual',
        yaxis_title='Return (%)', yaxis_ticksuffix='%',
        yaxis_range=[y_min - pad, y_max + pad],
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=500,
        showlegend=False, margin=dict(t=80, b=80, l=60, r=40),
    )
    fig.add_vline(x=2.5, line_dash='dot', line_color='#BBBBBB', line_width=1)
    fig.add_annotation(x=4.5, y=y_max + pad*0.6,
                       text='Active Return = 3 components + Residual ↓',
                       showarrow=False, font=dict(size=11, color='#7F8C8D'))
    return fig


def chart_sector_attribution(sector_df):
    """sector_df: self-computed Brinson; columns: sector / allocation / selection / interaction / total"""
    df = sector_df.copy().sort_values('total', ascending=True)
    df['sector_label'] = df['sector'].map(lambda x: f"{x} / {SECTOR_EN_MAP.get(x, '')}" if x in SECTOR_EN_MAP else x)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['allocation'].fillna(0) * 100,
        orientation='h', name='Allocation',
        marker_color=COLOR_INFO,
        hovertemplate='%{y}<br>Allocation: %{x:+.3f}%<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['selection'].fillna(0) * 100,
        orientation='h', name='Selection',
        marker_color=COLOR_ACCENT,
        hovertemplate='%{y}<br>Selection: %{x:+.3f}%<extra></extra>',
    ))
    if 'interaction' in df.columns:
        fig.add_trace(go.Bar(
            y=df['sector_label'], x=df['interaction'].fillna(0) * 100,
            orientation='h', name='Interaction',
            marker_color=COLOR_POS,
            hovertemplate='%{y}<br>Interaction: %{x:+.3f}%<extra></extra>',
        ))
    fig.update_layout(
        title='Per-Sector Brinson Decomposition (% contribution to Active)',
        xaxis_title='Contribution (%)', xaxis_ticksuffix='%',
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=560,
        barmode='relative',
        margin=dict(t=60, b=40, l=220, r=40),
    )
    return fig


def chart_monthly_trades(trades_data):
    monthly = trades_data['monthly']
    months = [str(m) for m in monthly.index]
    buy_n = monthly[('n', '買')].tolist() if ('n', '買') in monthly.columns else [0]*len(months)
    sell_n = monthly[('n', '賣')].tolist() if ('n', '賣') in monthly.columns else [0]*len(months)
    buy_amt = monthly[('amount', '買')].tolist() if ('amount', '買') in monthly.columns else [0]*len(months)
    sell_amt = monthly[('amount', '賣')].tolist() if ('amount', '賣') in monthly.columns else [0]*len(months)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=months, y=[v/1e6 for v in buy_amt], name='買進 ($M)',
        marker_color=COLOR_POS,
        text=[f'{int(n)} 筆' for n in buy_n], textposition='outside',
        cliponaxis=False,
        hovertemplate='%{x} 買進: $%{y:.1f}M<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        x=months, y=[-v/1e6 for v in sell_amt], name='賣出 ($M)',
        marker_color=COLOR_NEG,
        text=[f'{int(n)} 筆' for n in sell_n], textposition='outside',
        cliponaxis=False,
        hovertemplate='%{x} 賣出: $%{y:.1f}M<extra></extra>',
    ))
    fig.update_layout(
        title='月度交易節奏',
        xaxis_title='月份', yaxis_title='交易金額 (USD 百萬)',
        xaxis=dict(type='category'),
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=460,
        barmode='relative', margin=dict(t=60, b=40, l=60, r=40),
    )
    return fig


def chart_spy_percentile(quant):
    """持股於 SPY 母體的 YTD% 百分位 — 水平條"""
    df = quant['held_in_spy'].copy().sort_values('rank_full', ascending=True)
    # 處理 NaN rank
    df = df.dropna(subset=['rank_full'])
    if len(df) == 0:
        # Empty figure
        fig = go.Figure()
        fig.update_layout(title='無資料', template=PLOTLY_TEMPLATE, font=CHART_FONT, height=400)
        return fig

    colors = [COLOR_POS if v > 50 else COLOR_NEG for v in df['rank_full']]
    fig = go.Figure(go.Bar(
        y=df['name'], x=df['rank_full'], orientation='h',
        marker_color=colors,
        text=[f'{v:.0f}' for v in df['rank_full']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>SPY 分位: %{x:.1f}<br>YTD: %{customdata:+.2f}%<extra></extra>',
        customdata=df['tr_port'],
    ))
    fig.add_vline(x=50, line_dash='dash', line_color='#888',
                  annotation_text='隨機選股 (50)', annotation_position='top')
    fig.add_vline(x=75, line_dash='dot', line_color='#888',
                  annotation_text='前 1/4 (75)', annotation_position='top')
    fig.update_layout(
        title=f"持股於 SPY 母體 YTD% 百分位分布 (平均 {quant['mean_pct']:.1f}, 越高越好)",
        xaxis_title='SPY 百分位 (越高 = YTD 表現越好)',
        xaxis_range=[0, 108],
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=max(400, 22 * len(df) + 100),
        showlegend=False,
        margin=dict(t=90, b=40, l=180, r=80),
    )
    return fig


def chart_position_winrate(category_stats):
    """Stacked horizontal bar: winners vs losers count per category."""
    cats = [r for r in category_stats if r['cat'] != '合計']
    if not cats:
        return go.Figure()
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=[c['cat'] for c in cats], x=[c['Winners'] for c in cats],
        orientation='h', marker_color=COLOR_POS, name='獲利',
        text=[f"{c['Winners']} ({c['Winners']/c['Count']*100:.0f}%)" for c in cats],
        textposition='inside', textfont=dict(size=14, color='white'),
        hovertemplate='%{y}<br>獲利: %{x} 檔<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=[c['cat'] for c in cats], x=[c['Count']-c['Winners'] for c in cats],
        orientation='h', marker_color=COLOR_NEG, name='虧損',
        text=[f"{c['Count']-c['Winners']} ({(1-c['Winners']/c['Count'])*100:.0f}%)" for c in cats],
        textposition='inside', textfont=dict(size=14, color='white'),
        hovertemplate='%{y}<br>虧損: %{x} 檔<extra></extra>',
    ))
    fig.update_layout(
        title='持有部位勝率 (YTD): 期初持有 vs 期內新進',
        barmode='stack',
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=240,
        margin=dict(t=60, b=30, l=140, r=40),
        xaxis=dict(title='檔數', zeroline=True),
        showlegend=True,
        legend=dict(orientation='h', yanchor='bottom', y=1.05, xanchor='center', x=0.5),
    )
    return fig


def chart_winrate(trades_data):
    n_win = trades_data['n_winners']
    n_loss = trades_data['n_losers']
    fig = go.Figure(data=[go.Pie(
        labels=['獲利賣出', '虧損賣出'],
        values=[n_win, n_loss],
        marker_colors=[COLOR_POS, COLOR_NEG],
        textinfo='label+percent+value',
        hovertemplate='%{label}: %{value} 筆 (%{percent})<extra></extra>',
    )])
    fig.update_layout(
        title=f'賣出單筆勝率 (有實現損益的 {n_win+n_loss} 筆)',
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=440,
        margin=dict(t=60, b=40, l=60, r=40),
    )
    return fig


# =============================================================
# HTML build
# =============================================================
def build_html(d, results):
    print('  [HTML] 沿用 console 分析結果')
    perf = results['perf']
    holdings = results['holdings']
    pnl_d = holdings['pnl_decomp']
    daily = results['daily']['daily']
    sector_df = results['sector_df']                # Bloomberg 來源 (產業曝險用)
    brinson_df = results.get('attribution', sector_df)  # 自算 Brinson (歸因用)
    trades_data = results['trades']
    quant = results.get('quant')

    # 把自算 Brinson 的 sector_df 轉成 Bloomberg-style 欄位 + 值 (×100) 供細項表使用
    brinson_legacy = _convert_brinson_to_legacy(brinson_df)

    # Build all figures
    figs = {
        'return': chart_return_bar(perf),
        'mv': chart_daily_mv(daily, perf),
        'pnl_ts': chart_daily_pnl(daily),
        'contrib': chart_contributors(holdings['all_contributors'], holdings['quota_usd']),
        'top_holdings': chart_top_holdings(
            _with_held_at_start(
                _with_wt_bench(holdings['held_sorted'], d['bb_securities']),
                d['h_start'],
            ),
            _spy_universe(d['bb_securities'],
                          set(holdings['held_sorted']['ticker'].str.split().str[0].str.upper().str.strip())),
        ),
        'holdings_weights': chart_holdings_weights(_with_wt_bench(holdings['held_sorted'], d['bb_securities'])),
        'holdings_pnl': chart_holdings_pnl_pct(holdings['held_sorted']),
        'sector_combined': chart_sector_weight_return(sector_df),
        'sector_quadrant': chart_sector_quadrant(sector_df),
        'waterfall': chart_attribution_waterfall(perf),
        'sec_attr': chart_sector_attribution(brinson_df),
        'monthly': chart_monthly_trades(trades_data),
        'winrate': chart_winrate(trades_data),
    }
    if quant is not None:
        figs['quant_edge'] = chart_spy_percentile(quant)

    # Render chart divs
    divs = {k: fig_to_div(v, f'fig_{k}') for k, v in figs.items()}

    # Contributors 全表: 全部曾持有 ticker + 報酬率貢獻 (分母 = 額度)
    quota = holdings['quota_usd']
    contrib_disp = holdings['all_contributors'].copy()
    contrib_disp['P&L%'] = contrib_disp['TOTAL_PL'] / quota
    contrib_disp = contrib_disp[['ticker', 'STK_NAME', 'TOTAL_URCG', 'TOTAL_REALIZED',
                                  'TOTAL_DVD', 'TOTAL_PL', 'P&L%']]
    contrib_disp.columns = ['Ticker', '公司', 'URCG (YTD)', 'RCG (YTD)', 'DVD (YTD)', 'P&L (YTD)', 'P&L% (YTD)']
    for c in ['URCG (YTD)', 'RCG (YTD)', 'DVD (YTD)', 'P&L (YTD)']:
        contrib_disp[c] = contrib_disp[c].apply(lambda x: fmt_usd(x, 0))
    contrib_disp['P&L% (YTD)'] = contrib_disp['P&L% (YTD)'].apply(lambda x: f'{x*100:+.3f}%')
    all_contrib_html = df_to_html_table(contrib_disp)

    # 可展開的 sector → 證券 細項表 (兩個版本); 用自算 Brinson 的 legacy-format
    sec_attr_held_html = _build_expandable_attribution_table(
        brinson_legacy, d['bb_securities'], held_only=True, table_id='attr-table-held'
    )
    sec_attr_table_html = _build_expandable_attribution_table(
        brinson_legacy, d['bb_securities'], held_only=False, table_id='attr-table-all'
    )

    # End holdings full table
    end_holdings = holdings['held_sorted'][['ticker', 'STK_NAME', 'TOTAL_SHARES', 'TOTAL_COST',
                                              'TOTAL_MV', 'TOTAL_PL', 'weight']].copy()
    # P&L% = P&L / 庫存成本 (個股投報率)
    end_holdings['pnl_pct'] = end_holdings['TOTAL_PL'] / end_holdings['TOTAL_COST'].replace(0, np.nan)
    end_holdings.columns = ['Ticker', '公司', '股數', '庫存成本', '市值', 'P&L (YTD)', '權重', 'P&L% (YTD)']
    end_holdings['股數'] = end_holdings['股數'].apply(lambda x: f'{int(x):,}')
    for c in ['庫存成本', '市值', 'P&L (YTD)']:
        end_holdings[c] = end_holdings[c].apply(lambda x: fmt_usd(x, 0))
    end_holdings['權重'] = end_holdings['權重'].apply(lambda x: fmt_pct(x, 2, sign=False))
    end_holdings['P&L% (YTD)'] = end_holdings['P&L% (YTD)'].apply(lambda x: fmt_pct(x, 2) if pd.notna(x) else 'n/a')
    end_holdings_html = df_to_html_table(end_holdings)

    period_start = perf['period_start']
    period_end = perf['period_end']

    # Tabs definition
    tabs = [
        ('overview', 'Overview'),
        ('contrib', 'Contributors'),
        ('top_holdings', 'Holdings'),
        ('sector', 'Sector Exposure'),
        ('attribution', 'Attribution'),
        ('trading', 'Trading'),
        ('quant_edge', 'Quant Edge'),
        ('notes', 'Glossary'),
    ]

    plotly_cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'

    tab_buttons = '\n'.join([
        f'<button class="tab-btn{" active" if i==0 else ""}" data-target="{tid}">{label}</button>'
        for i, (tid, label) in enumerate(tabs)
    ])

    # ----- Content for each tab -----
    overview_html = f"""
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">組合報酬 (自算 TWRR)</div>
        <div class="kpi-value">{fmt_pct(perf['port_return'])}</div>
        <div class="kpi-sub">daily 累乘, CF end-of-day</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">Modified Dietz (自算)</div>
        <div class="kpi-value">{fmt_pct(perf.get('md_return'))}</div>
        <div class="kpi-sub">TWRR 近似法</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">簡單法 (P&L/額度)</div>
        <div class="kpi-value">{fmt_pct(perf.get('simple_return'))}</div>
        <div class="kpi-sub">額度 $665M (固定)</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">SPY 基準</div>
        <div class="kpi-value">{fmt_pct(perf['spy_return'])}</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">Active Return</div>
        <div class="kpi-value">{fmt_pct(perf['active_return'])}</div>
        <div class="kpi-sub">vs SPY</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">Active Share (期末)</div>
        <div class="kpi-value">{fmt_pct(perf.get('active_share'), 1, sign=False)}</div>
        <div class="kpi-sub">end-of-period, 期間平均 {fmt_pct(perf.get('active_share_avg'), 1, sign=False)}</div>
    </div>
</div>

<h3>報酬率比較 (各口徑)</h3>
{divs['return']}

<p class="narrative">
    組合報酬 (自算 daily TWRR) <strong>{fmt_pct(perf['port_return'])}</strong> vs SPY {fmt_pct(perf['spy_return'])}
    = Active <strong>{fmt_pct(perf['active_return'])}</strong>
    (Bloomberg TWRR Reference: {fmt_pct(perf.get('bb_port_return'))}, 差異見「說明」頁 CF timing 對比).
    Modified Dietz 自算 {fmt_pct(perf.get('md_return'))} 為整期一次性近似, 與每日累乘 TWRR 應略有差異.<br>
    Active Share <strong>{fmt_pct(perf.get('active_share'), 1, sign=False)}</strong> 表示組合裡有此比例的權重與 SPY 配置不同.
    Active 拆解 (Brinson-Fachler 3-comp + Carino): Allocation {fmt_pct(perf['industry_active'])} + Selection {fmt_pct(perf['selection_active'])} + Interaction {fmt_pct(perf.get('interaction_active'))} + Residual {fmt_pct(perf.get('residual_active'))}.
</p>
"""

    top1 = holdings['held_sorted'].iloc[0]
    top1_ticker = str(top1['ticker']).split()[0]
    # ----- 貢獻者亮點計算 -----
    h_end_all = d['h_end'].copy()
    pnl_sorted = h_end_all.sort_values('TOTAL_PL', ascending=False).reset_index(drop=True)
    total_pnl_contrib = pnl_d['total']
    n_total_contrib = len(pnl_sorted[pnl_sorted['TOTAL_PL'].notna()])
    n_winners = int((pnl_sorted['TOTAL_PL'] > 0).sum())
    n_losers = int((pnl_sorted['TOTAL_PL'] < 0).sum())
    top1 = pnl_sorted.iloc[0]
    top1_ticker = str(top1['ticker']).split()[0]
    top1_pnl = top1['TOTAL_PL']
    top1_share = top1_pnl / total_pnl_contrib if total_pnl_contrib else 0
    top5_pnl = pnl_sorted.head(5)['TOTAL_PL'].sum()
    top5_share = top5_pnl / total_pnl_contrib if total_pnl_contrib else 0
    top10_pnl = pnl_sorted.head(10)['TOTAL_PL'].sum()
    top10_share = top10_pnl / total_pnl_contrib if total_pnl_contrib else 0
    winners_total = pnl_sorted[pnl_sorted['TOTAL_PL'] > 0]['TOTAL_PL'].sum()
    losers_total = pnl_sorted[pnl_sorted['TOTAL_PL'] < 0]['TOTAL_PL'].sum()
    avg_winner = winners_total / n_winners if n_winners else 0
    avg_loser = losers_total / n_losers if n_losers else 0
    win_loss_ratio = abs(winners_total / losers_total) if losers_total else None
    top1_to_bot1_ratio = abs(top1_pnl / pnl_sorted.iloc[-1]['TOTAL_PL']) if pnl_sorted.iloc[-1]['TOTAL_PL'] else None

    top5_tickers = ' / '.join(pnl_sorted.head(5)['ticker'].str.split().str[0].tolist())

    contrib_html = f"""
<h3>★ 模型投組貢獻亮點</h3>
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">最大貢獻者</div>
        <div class="kpi-value">{top1_ticker}</div>
        <div class="kpi-sub">{fmt_usd_m(top1_pnl, 1)} · 佔總 P&L {fmt_pct(top1_share, 1, sign=False)}</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">Top 5 累計 P&L</div>
        <div class="kpi-value">{fmt_usd_m(top5_pnl, 0)}</div>
        <div class="kpi-sub">佔總 P&L {fmt_pct(top5_share, 1, sign=False)}</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">Top 10 累計 P&L</div>
        <div class="kpi-value">{fmt_usd_m(top10_pnl, 0)}</div>
        <div class="kpi-sub">佔總 P&L {fmt_pct(top10_share, 1, sign=False)}</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">獲利檔數</div>
        <div class="kpi-value">{n_winners} / {n_total_contrib}</div>
        <div class="kpi-sub">{n_winners/n_total_contrib*100:.1f}% 獲利率</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">獲利:虧損 金額比</div>
        <div class="kpi-value">{f'{win_loss_ratio:.1f}x' if win_loss_ratio else 'n/a'}</div>
        <div class="kpi-sub">{fmt_usd_m(winners_total, 0)} / {fmt_usd_m(losers_total, 0)}</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">最大獲利 / 最大虧損</div>
        <div class="kpi-value">{f'{top1_to_bot1_ratio:.1f}x' if top1_to_bot1_ratio else 'n/a'}</div>
        <div class="kpi-sub">{fmt_usd_m(top1_pnl, 1)} vs {fmt_usd_m(pnl_sorted.iloc[-1]['TOTAL_PL'], 1)}</div>
    </div>
</div>

<p class="narrative">
    <strong>Top 5 ({top5_tickers}) 共貢獻 {fmt_usd_m(top5_pnl, 0)} (佔總 P&L {fmt_pct(top5_share, 1, sign=False)})</strong>,
    顯示模型對少數核心強股的捕捉能力。
    全組合 {n_winners}/{n_total_contrib} 部位獲利, 獲利金額是虧損金額的
    <strong>{f'{win_loss_ratio:.1f}'+'x' if win_loss_ratio else 'n/a'}</strong> ({fmt_usd_m(winners_total, 0)} vs {fmt_usd_m(losers_total, 0)}),
    展現「獲利大、虧損小」的不對稱風險報酬。
    最大獲利者 ({top1_ticker} {fmt_usd_m(top1_pnl, 1)}) 約為最大虧損的
    <strong>{f'{top1_to_bot1_ratio:.0f}'+'x' if top1_to_bot1_ratio else 'n/a'}</strong>。
</p>

{divs['contrib']}

<h3>全部曾持有 ticker YTD 貢獻 ({len(holdings['all_contributors'])} 檔, 按 YTD P&L 降冪)</h3>
<p class="narrative method-note">
    口徑: YTD 來自 Reset_TOTAL_PL 欄位 (每年 1/1 reset).
    P&L% = YTD P&L / 額度 ${quota/1e6:.0f}M;
    Σ YTD P&L = {fmt_usd_m(holdings['pnl_decomp']['total'], 0)}, Σ P&L% = {fmt_pct(holdings['pnl_decomp']['total']/quota)}.
</p>
{all_contrib_html}
"""

    # alpha stats (trend line slope + ρ) 用於附註
    held_w = _with_wt_bench(holdings['held_sorted'], d['bb_securities'])
    spy_u = _spy_universe(d['bb_securities'],
                          set(holdings['held_sorted']['ticker'].str.split().str[0].str.upper().str.strip()))
    slope, rho, n_total = _alpha_stats(held_w, spy_u)
    if slope is not None:
        ic_strength = ('強' if abs(rho) >= 0.30 else ('中' if abs(rho) >= 0.15 else '弱'))
        ic_dir = ('正向' if rho > 0 else '負向') if rho != 0 else '中性'
        slope_interp = ('每多超配 1%, 平均年報酬高出'
                        f' <strong>{slope:+.2f} pp</strong>' if slope > 0
                        else f'每多超配 1%, 平均年報酬<strong>低</strong> {abs(slope):.2f} pp')
        alpha_note = f"""<p class="narrative method-note" style="margin-top:6px;">
    <strong>趨勢線解讀 (n={n_total} 檔, 含 SPY 全母體 + 持有)</strong>:
    <code>slope = {slope:+.2f}</code> · <code>Pearson ρ = {rho:+.3f}</code> ({ic_strength}{ic_dir}).<br>
    <strong>Slope</strong> ({slope_interp}): 線性回歸斜率, 反映「主動配置強度 → 報酬」的邊際效應.<br>
    <strong>ρ (相關係數)</strong>: 衡量 Active Weight 與 P&L% 的線性相關度, 範圍 −1~+1.
    經驗分級: |ρ|≥0.30 強 alpha · 0.15~0.30 中等 · &lt;0.15 弱/無關.<br>
    本期 <strong>ρ = {rho:+.3f}</strong> → 主動配置與報酬呈<strong>{ic_strength}{ic_dir}</strong>相關.
</p>"""
    else:
        alpha_note = ''

    top_holdings_html = f"""
{divs['top_holdings']}
{alpha_note}
<div style="display:flex; gap:12px; align-items:flex-start; flex-wrap:nowrap; margin-top:14px;">
    <div style="flex:1 1 0; min-width:0;">{divs['holdings_weights']}</div>
    <div style="flex:1 1 0; min-width:0;">{divs['holdings_pnl']}</div>
</div>
<p class="narrative">
    Top 10 集中 <strong>{fmt_pct(holdings['top10_w'], 1, sign=False)}</strong>;
    有效持股數 {holdings['eff_n']:.1f} (1/Σw²)。
</p>
<details style="margin-top:18px"><summary>持股明細 ({len(holdings['held_sorted'])} 檔)</summary>
{end_holdings_html}
</details>
"""

    # Top overweight / avoided sectors
    over = sector_df.sort_values('wt_active', ascending=False).head(3)
    under = sector_df.sort_values('wt_active', ascending=True).head(3)
    over_desc = ' / '.join(f"{r['name']} {r['wt_active']:+.1f}%" for _, r in over.iterrows())
    under_desc = ' / '.join(f"{r['name']} {r['wt_active']:+.1f}%" for _, r in under.iterrows())
    # 找出報酬最佳/最差的 sector
    sec_sorted_by_active = sector_df.sort_values('tr_active', ascending=False)
    best_r = sec_sorted_by_active.iloc[0]
    worst_r = sec_sorted_by_active.iloc[-1]
    # Sector trend stats for narrative
    sec_trend = getattr(figs['sector_quadrant'], '_sector_trend', (None, None, 0))
    sec_slope, sec_rho, sec_n = sec_trend
    if sec_slope is not None:
        sec_ic_strength = '強' if abs(sec_rho) >= 0.30 else ('中' if abs(sec_rho) >= 0.15 else '弱')
        sec_ic_dir = '正向' if sec_rho > 0 else '負向'
        sec_alpha_note = f"""<p class="narrative method-note">
    <strong>趨勢線解讀 (n={sec_n} sectors)</strong>:
    <code>slope = {sec_slope:+.2f}</code> · <code>Pearson ρ = {sec_rho:+.3f}</code> ({sec_ic_strength}{sec_ic_dir}).<br>
    <strong>Slope</strong>: 每多超配 1%, 產業 Active Return 平均高出 <strong>{sec_slope:+.2f} pp</strong>.<br>
    <strong>ρ</strong>: Active Weight 與 Active Return 線性相關度, |ρ|≥0.30 為強 alpha.<br>
    本期 <strong>ρ = {sec_rho:+.3f}</strong> → 產業配置與超額報酬呈<strong>{sec_ic_strength}{sec_ic_dir}</strong>相關.
</p>"""
    else:
        sec_alpha_note = ''

    sector_html = f"""
<h3>產業 Quadrant: 重押產業 vs 超額報酬</h3>
{divs['sector_quadrant']}
{sec_alpha_note}

<h3>產業權重 vs 報酬率</h3>
{divs['sector_combined']}
<p class="narrative">
    <strong>顯著超配</strong>: {over_desc}<br>
    <strong>顯著低配</strong>: {under_desc}<br>
    <strong>本組合表現最強 sector</strong>: {best_r['name']} rP {best_r['tr_port']:+.2f}% vs SPY {best_r['tr_bench']:+.2f}% (Active {best_r['tr_active']:+.2f}%)<br>
    <strong>本組合表現最弱 sector</strong>: {worst_r['name']} rP {worst_r['tr_port']:+.2f}% vs SPY {worst_r['tr_bench']:+.2f}% (Active {worst_r['tr_active']:+.2f}%)
</p>
"""

    attribution_html = f"""
<h3>Active Return Waterfall</h3>
{divs['waterfall']}
<p class="narrative">
    純自算 Brinson-Fachler 3-component + Carino linking 拆解 Active Return:<br>
    <strong>Allocation ({fmt_pct(perf['industry_active'])})</strong>:
    產業配置貢獻 — (w<sub>P</sub>−w<sub>B</sub>) × (r<sub>B,i</sub>−r<sub>B,total</sub>), 超配跑贏大盤的產業為正.<br>
    <strong>Selection ({fmt_pct(perf['selection_active'])})</strong>:
    各產業內選股貢獻 — w<sub>B,i</sub> × (r<sub>P,i</sub>−r<sub>B,i</sub>), 以基準權重加權的個股 alpha.<br>
    <strong>Interaction ({fmt_pct(perf.get('interaction_active'))})</strong>:
    交互作用 — (w<sub>P</sub>−w<sub>B</sub>) × (r<sub>P,i</sub>−r<sub>B,i</sub>), 既超配又選對的乘積紅利.<br>
    <strong>Residual ({fmt_pct(perf.get('residual_active'))})</strong>:
    Carino 多期累乘浮點誤差 + w<sub>B</sub> 子期間近似誤差, 應接近 0 (本期 ±0.5% 內).
</p>

<h3>Per-Sector Decomposition</h3>
{divs['sec_attr']}

<h3>Held positions detail (click sector row to expand)</h3>
{sec_attr_held_html}
<p class="narrative method-note">
    Click any sector row to expand to held positions in that sector (wt_port &gt; 0).
</p>

<h3>Full SPY constituents detail (click sector row to expand)</h3>
{sec_attr_table_html}
<p class="narrative method-note">
    Expanding shows all SPY constituents (including those not held).
    CTR (Contribution to Return) = sector total contribution = Allocation + Selection + Interaction.
</p>
"""

    # ---- 持有部位勝率: 期初持有 vs 期內新進 (YTD P&L 為勝負判斷) ----
    all_c = holdings['all_contributors'].copy()
    h_start_tickers = set(d['h_start'][d['h_start']['TOTAL_MV'] > 0]['ticker'])
    h_end_held_tickers = set(d['h_end'][d['h_end']['TOTAL_MV'] > 0]['ticker'])
    # trades 細分: 該 ticker 期內有沒有買 / 賣
    tr_for_cat = d['trades']
    tickers_with_buy = set(tr_for_cat[tr_for_cat['side'] == '買']['ticker'].unique())
    tickers_with_sell = set(tr_for_cat[tr_for_cat['side'] == '賣']['ticker'].unique())

    def _detail_cat(tk):
        in_start = tk in h_start_tickers
        had_buy = tk in tickers_with_buy
        had_sell = tk in tickers_with_sell
        still_held = tk in h_end_held_tickers
        if in_start:
            if not had_buy and not had_sell:
                return '期初持有 — 期內無交易'
            if had_buy:
                return '期初持有 — 期內加碼 (有買)'
            return '期初持有 — 期內僅減碼 (無買, 有賣)'
        # 期內新進
        if still_held:
            return '期內新進 — 期末仍持有'
        return '期內新進 — 期內已賣光'

    all_c['_detail'] = all_c['ticker'].apply(_detail_cat)
    all_c['_cat'] = all_c['ticker'].apply(lambda t: '期初持有' if t in h_start_tickers else '期內新進')
    cat_rows = []
    for cat in ['期初持有', '期內新進']:
        sub = all_c[all_c['_cat'] == cat]
        n = len(sub)
        if n == 0:
            continue
        w = (sub['TOTAL_PL'] > 0).sum()
        cat_rows.append({
            'cat': cat,
            'Count': n,
            'Winners': int(w),
            'Win%': w / n * 100,
            'Avg P&L': sub['TOTAL_PL'].mean(),
            'Total P&L': sub['TOTAL_PL'].sum(),
        })
    # 合計列
    if cat_rows:
        n_all = len(all_c)
        w_all = (all_c['TOTAL_PL'] > 0).sum()
        cat_rows.append({
            'cat': '合計',
            'Count': n_all,
            'Winners': int(w_all),
            'Win%': w_all / n_all * 100,
            'Avg P&L': all_c['TOTAL_PL'].mean(),
            'Total P&L': all_c['TOTAL_PL'].sum(),
        })
    pos_winrate_table_df = pd.DataFrame([{
        'Category': r['cat'],
        'Count': r['Count'],
        'Winners': r['Winners'],
        'Win%': f"{r['Win%']:.1f}%",
        'Avg P&L (YTD)': fmt_usd(r['Avg P&L'], 0),
        'Total P&L (YTD)': fmt_usd(r['Total P&L'], 0),
    } for r in cat_rows])
    pos_winrate_table_html = df_to_html_table(pos_winrate_table_df)
    pos_winrate_chart_div = fig_to_div(chart_position_winrate(cat_rows), 'fig_pos_winrate')

    # ---- 細分版: 期初/期內 × 加碼/減碼/無動作 ----
    detail_order = [
        '期初持有 — 期內無交易',
        '期初持有 — 期內加碼 (有買)',
        '期初持有 — 期內僅減碼 (無買, 有賣)',
        '期內新進 — 期末仍持有',
        '期內新進 — 期內已賣光',
    ]
    detail_rows = []
    for cat in detail_order:
        sub = all_c[all_c['_detail'] == cat]
        n = len(sub)
        if n == 0:
            continue
        w = int((sub['TOTAL_PL'] > 0).sum())
        detail_rows.append({
            'Category': cat,
            'Count': n,
            'Winners': w,
            'Win%': f"{w/n*100:.1f}%",
            'Avg P&L (YTD)': fmt_usd(sub['TOTAL_PL'].mean(), 0),
            'Total P&L (YTD)': fmt_usd(sub['TOTAL_PL'].sum(), 0),
        })
    detail_rows.append({
        'Category': '合計',
        'Count': len(all_c),
        'Winners': int((all_c['TOTAL_PL'] > 0).sum()),
        'Win%': f"{(all_c['TOTAL_PL']>0).sum()/len(all_c)*100:.1f}%",
        'Avg P&L (YTD)': fmt_usd(all_c['TOTAL_PL'].mean(), 0),
        'Total P&L (YTD)': fmt_usd(all_c['TOTAL_PL'].sum(), 0),
    })
    pos_winrate_detail_html = df_to_html_table(pd.DataFrame(detail_rows))

    # Trade summary text
    n_sp = trades_data['n_winners'] + trades_data['n_losers']
    win_rate = trades_data['n_winners']/n_sp*100 if n_sp else 0
    pl_ratio = abs(trades_data['avg_win']/trades_data['avg_loss']) if trades_data['avg_loss'] else None

    # ----- 交易亮點計算 -----
    swp = trades_data['sells_with_pnl']
    winners = swp[swp['realized_pnl'] > 0]
    losers = swp[swp['realized_pnl'] < 0]
    winners_total = winners['realized_pnl'].sum()
    losers_total = losers['realized_pnl'].sum()
    biggest_win = winners.nlargest(1, 'realized_pnl').iloc[0] if len(winners) else None
    biggest_loss = losers.nsmallest(1, 'realized_pnl').iloc[0] if len(losers) else None
    top5_wins = winners.nlargest(5, 'realized_pnl')
    top5_wins_sum = top5_wins['realized_pnl'].sum()
    top5_wins_share = top5_wins_sum / winners_total if winners_total else 0
    realized_amount_ratio = abs(winners_total / losers_total) if losers_total else None

    # 各 ticker 累計實現 P&L
    by_ticker = swp.groupby('ticker').agg(
        n_sells=('realized_pnl', 'count'),
        sum_pnl=('realized_pnl', 'sum'),
    ).reset_index().sort_values('sum_pnl', ascending=False)
    best_ticker = by_ticker.iloc[0] if len(by_ticker) else None

    # Top 10 winning sells table
    top10_wins = winners.nlargest(10, 'realized_pnl')[['交易日期', 'ticker', 'qty', 'price', 'realized_pnl']].copy()
    top10_wins.columns = ['日期', 'Ticker', '股數', '單價', '價差損益']
    top10_wins['日期'] = top10_wins['日期'].dt.strftime('%Y-%m-%d')
    top10_wins['股數'] = top10_wins['股數'].apply(lambda x: f'{int(x):,}')
    top10_wins['單價'] = top10_wins['單價'].apply(lambda x: f'{x:.2f}')
    top10_wins['價差損益'] = top10_wins['價差損益'].apply(lambda x: fmt_usd(x, 0))
    top10_wins_html = df_to_html_table(top10_wins)

    # Top by-ticker table
    top10_by_ticker = by_ticker.head(10).copy()
    top10_by_ticker.columns = ['Ticker', '賣出筆數', '累計實現損益']
    top10_by_ticker['累計實現損益'] = top10_by_ticker['累計實現損益'].apply(lambda x: fmt_usd(x, 0))
    top10_by_ticker_html = df_to_html_table(top10_by_ticker)

    # 找出 high-win-rate 類別作為標題敘述
    held_rate = next((r['Win%'] for r in cat_rows if r['cat']=='期初持有'), None)
    new_rate = next((r['Win%'] for r in cat_rows if r['cat']=='期內新進'), None)
    all_rate = next((r['Win%'] for r in cat_rows if r['cat']=='合計'), None)

    trading_html = f"""
<h3>★ 持有部位勝率: 買進 vs 期初持有 (YTD)</h3>
{pos_winrate_table_html}
{pos_winrate_chart_div}
<p class="narrative">
    模型 YTD 整體勝率 <strong>{all_rate:.1f}%</strong>:
    期初持有 <strong>{held_rate:.1f}%</strong>, 期內新進 <strong>{new_rate:.1f}%</strong>.
    勝負判斷以該 ticker YTD P&L 是否 &gt; 0 為準 (Reset_TOTAL_PL).
</p>

<h4>細分: 期內加碼 / 減碼 / 無交易</h4>
{pos_winrate_detail_html}
<p class="narrative method-note">
    細分依「該 ticker 期內是否有買 / 賣交易」進一步拆解.
    「加碼」= 有買進 (可能也有賣); 「僅減碼」= 只有賣出無買進.
    「期末仍持有」= h_end TOTAL_MV &gt; 0; 「已賣光」= h_end TOTAL_MV = 0.
</p>

<h3>★ 模型交易亮點</h3>
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">單筆最大獲利</div>
        <div class="kpi-value">{str(biggest_win['ticker']).split()[0] if biggest_win is not None else 'n/a'}</div>
        <div class="kpi-sub">{fmt_usd_m(biggest_win['realized_pnl'], 1) if biggest_win is not None else 'n/a'} · {biggest_win['交易日期'].strftime('%Y-%m-%d') if biggest_win is not None else ''}</div>
    </div>
    <div class="kpi-card neg">
        <div class="kpi-label">單筆最大虧損</div>
        <div class="kpi-value">{str(biggest_loss['ticker']).split()[0] if biggest_loss is not None else 'n/a'}</div>
        <div class="kpi-sub">{fmt_usd_m(biggest_loss['realized_pnl'], 1) if biggest_loss is not None else 'n/a'} · {biggest_loss['交易日期'].strftime('%Y-%m-%d') if biggest_loss is not None else ''}</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">Top 5 獲利賣出累計</div>
        <div class="kpi-value">{fmt_usd_m(top5_wins_sum, 0)}</div>
        <div class="kpi-sub">佔獲利賣出 {fmt_pct(top5_wins_share, 1, sign=False)}</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">獲利賣出總金額</div>
        <div class="kpi-value">{fmt_usd_m(winners_total, 0)}</div>
        <div class="kpi-sub">{trades_data['n_winners']} 筆獲利賣出</div>
    </div>
    <div class="kpi-card neg">
        <div class="kpi-label">虧損賣出總金額</div>
        <div class="kpi-value">{fmt_usd_m(losers_total, 1)}</div>
        <div class="kpi-sub">{trades_data['n_losers']} 筆虧損賣出</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">獲利/虧損 金額比</div>
        <div class="kpi-value">{f'{realized_amount_ratio:.1f}x' if realized_amount_ratio else 'n/a'}</div>
        <div class="kpi-sub">{fmt_usd_m(winners_total, 0)} / {fmt_usd_m(losers_total, 1)}</div>
    </div>
</div>
<p class="narrative">
    模型展現「<strong>讓贏家奔跑、迅速停損輸家</strong>」特性: 雖然獲利筆數 ({trades_data['n_winners']}) 少於虧損筆數 ({trades_data['n_losers']}),
    但<strong>獲利金額 {fmt_usd_m(winners_total, 0)} 是虧損金額 {fmt_usd_m(abs(losers_total), 0)} 的 {f'{realized_amount_ratio:.1f}'+'x' if realized_amount_ratio else 'n/a'}</strong>;
    Top 5 賣出獲利共 {fmt_usd_m(top5_wins_sum, 0)} 已佔全部獲利的 {fmt_pct(top5_wins_share, 1, sign=False)},
    顯示模型抓到的強股能讓部位充分發酵, 而非淺嚐輒止。
    最佳 ticker: <strong>{str(best_ticker['ticker']).split()[0] if best_ticker is not None else 'n/a'}</strong>
    累計實現 {fmt_usd_m(best_ticker['sum_pnl'], 0) if best_ticker is not None else 'n/a'} (跨 {int(best_ticker['n_sells']) if best_ticker is not None else 0} 筆賣出)。
</p>

<div class="two-col">
<div>
<h3>Top 10 獲利賣出明細</h3>
{top10_wins_html}
</div>
<div>
<h3>Top 10 Ticker 累計實現損益</h3>
{top10_by_ticker_html}
</div>
</div>

<h3>月度交易節奏</h3>
{divs['monthly']}
<p class="narrative">
    買進 {trades_data['n_buys']} 筆 共 {fmt_usd_m(trades_data['amount_buy'], 0)};
    賣出 {trades_data['n_sells']} 筆 共 {fmt_usd_m(trades_data['amount_sell'], 0)};
    One-way turnover {fmt_pct(trades_data['one_way_turnover'])};
    總交易費用 {fmt_usd(trades_data['total_fees'])}。
</p>

"""

    # ----- 量化 Edge tab content -----
    if quant is not None and 'quant_edge' in figs:
        # Build held-in-SPY table (期末仍持有 ∩ SPY)
        his_disp = quant['held_in_spy'].copy()
        his_disp = his_disp[['name', 'sector', 'wt_port', 'tr_port', 'rank_full']].copy()
        his_disp.columns = ['名稱', 'Sector', '組合權重%', 'YTD%', 'SPY 百分位']
        his_disp['組合權重%'] = his_disp['組合權重%'].apply(lambda x: f'{x:.2f}' if pd.notna(x) else 'n/a')
        his_disp['YTD%'] = his_disp['YTD%'].apply(lambda x: f'{x:+.2f}' if pd.notna(x) else 'n/a')
        his_disp['SPY 百分位'] = his_disp['SPY 百分位'].apply(lambda x: f'{x:.1f}' if pd.notna(x) else 'n/a')
        his_table_html = df_to_html_table(his_disp)

        # Missed / avoided
        missed_disp = quant['missed_top'].copy()
        missed_disp.columns = ['名稱', 'Sector', 'SPY 權重%', 'YTD%', 'SPY 百分位']
        for c in ['SPY 權重%']:
            missed_disp[c] = missed_disp[c].apply(lambda x: f'{x:.3f}' if pd.notna(x) else 'n/a')
        missed_disp['YTD%'] = missed_disp['YTD%'].apply(lambda x: f'{x:+.2f}' if pd.notna(x) else 'n/a')
        missed_disp['SPY 百分位'] = missed_disp['SPY 百分位'].apply(lambda x: f'{x:.1f}' if pd.notna(x) else 'n/a')
        missed_table_html = df_to_html_table(missed_disp)

        avoided_disp = quant['avoided_bottom'].copy()
        avoided_disp.columns = ['名稱', 'Sector', 'SPY 權重%', 'YTD%', 'SPY 百分位']
        for c in ['SPY 權重%']:
            avoided_disp[c] = avoided_disp[c].apply(lambda x: f'{x:.3f}' if pd.notna(x) else 'n/a')
        avoided_disp['YTD%'] = avoided_disp['YTD%'].apply(lambda x: f'{x:+.2f}' if pd.notna(x) else 'n/a')
        avoided_disp['SPY 百分位'] = avoided_disp['SPY 百分位'].apply(lambda x: f'{x:.1f}' if pd.notna(x) else 'n/a')
        avoided_table_html = df_to_html_table(avoided_disp)

        # 亮點: 持有的 SPY Top 20 漲幅 + 避開的 SPY Bottom 20 跌幅
        held_top20_disp = quant['held_top20'][['name', 'sector', 'wt_port', 'tr_port', 'rank_full']].copy()
        held_top20_disp.columns = ['名稱', 'Sector', '組合權重%', 'YTD%', 'SPY 百分位']
        held_top20_disp['組合權重%'] = held_top20_disp['組合權重%'].apply(lambda x: f'{x:.2f}' if pd.notna(x) else 'n/a')
        held_top20_disp['YTD%'] = held_top20_disp['YTD%'].apply(lambda x: f'{x:+.2f}' if pd.notna(x) else 'n/a')
        held_top20_disp['SPY 百分位'] = held_top20_disp['SPY 百分位'].apply(lambda x: f'{x:.1f}' if pd.notna(x) else 'n/a')
        held_top20_table_html = df_to_html_table(held_top20_disp) if len(held_top20_disp) > 0 else '<p>(無)</p>'

        avoided_bot20_disp = quant['avoided_bot20'][['name', 'sector', 'wt_bench', 'tr_bench', 'rank_full']].copy()
        avoided_bot20_disp.columns = ['名稱', 'Sector', 'SPY 權重%', 'YTD%', 'SPY 百分位']
        avoided_bot20_disp['SPY 權重%'] = avoided_bot20_disp['SPY 權重%'].apply(lambda x: f'{x:.3f}' if pd.notna(x) else 'n/a')
        avoided_bot20_disp['YTD%'] = avoided_bot20_disp['YTD%'].apply(lambda x: f'{x:+.2f}' if pd.notna(x) else 'n/a')
        avoided_bot20_disp['SPY 百分位'] = avoided_bot20_disp['SPY 百分位'].apply(lambda x: f'{x:.1f}' if pd.notna(x) else 'n/a')
        avoided_bot20_table_html = df_to_html_table(avoided_bot20_disp) if len(avoided_bot20_disp) > 0 else '<p>(無)</p>'

        bright = quant['bright']

        # IC 強弱判讀
        ic_full = quant.get('ic_spearman')
        ic_strength = ('強' if ic_full and abs(ic_full) >= 0.10 else ('中' if ic_full and abs(ic_full) >= 0.05 else '弱')) if ic_full is not None else 'n/a'
        # vs Random 多空捕捉統計表
        bright_table_rows = []
        for N in [10, 20, 50]:
            b = bright[N]
            m_hit = f"{b['multiplier_hit']:.1f}x" if b.get('multiplier_hit') else 'n/a'
            m_av = f"{b['multiplier_avoid']:.1f}x" if b.get('multiplier_avoid') else 'n/a'
            bright_table_rows.append(
                f"<tr><td>Top {N}</td><td>{b['held_in_top']} / {N}</td>"
                f"<td>{b['expected_hit']:.2f}</td><td><strong>{m_hit}</strong></td>"
                f"<td>{b['avoided_bottom']} / {N}</td>"
                f"<td>{b['expected_avoid']:.2f}</td><td><strong>{m_av}</strong></td></tr>"
            )
        bright_table_html = (
            '<table class="data-table" style="margin-top:8px;">'
            '<thead><tr>'
            '<th rowspan="2">N</th>'
            '<th colspan="3" style="text-align:center;background:#E8F5E9;">強漲股捕捉</th>'
            '<th colspan="3" style="text-align:center;background:#FFF3E0;">弱跌股迴避</th>'
            '</tr><tr>'
            '<th>命中</th><th>隨機期望</th><th>倍數</th>'
            '<th>迴避</th><th>隨機期望</th><th>倍數</th>'
            '</tr></thead><tbody>' + ''.join(bright_table_rows) + '</tbody></table>'
        )

        quant_edge_html = f"""
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">命中率 (在倉 ∩ SPY)</div>
        <div class="kpi-value">{fmt_pct(quant['hit_rate'], 1)}</div>
        <div class="kpi-sub">{quant['n_profitable_in_spy']} 獲利 / {quant['n_held_in_spy']} 檔</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">平均 SPY 百分位</div>
        <div class="kpi-value">{quant['mean_pct']:.1f}</div>
        <div class="kpi-sub">前 1/4 占 {quant['pct_top25']:.0f}% · 50 = 隨機</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">等權超額 vs SPY 母體</div>
        <div class="kpi-value">{(quant['port_avg_uw'] - quant['spy_avg_uw']):+.2f}%</div>
        <div class="kpi-sub">持股 {quant['port_avg_uw']:+.1f}% vs SPY {quant['spy_avg_uw']:+.1f}%</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">強漲股捕捉 (Top 20)</div>
        <div class="kpi-value">{bright[20]['held_in_top']} / 20</div>
        <div class="kpi-sub">{('{:.1f}x random'.format(bright[20]['multiplier_hit'])) if bright[20].get('multiplier_hit') else 'n/a'}</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">弱跌股迴避 (Bottom 20)</div>
        <div class="kpi-value">{bright[20]['avoided_bottom']} / 20</div>
        <div class="kpi-sub">{('{:.1f}x random'.format(bright[20]['multiplier_avoid'])) if bright[20].get('multiplier_avoid') else 'n/a'}</div>
    </div>
    <div class="kpi-card {'pos' if (ic_full or 0) > 0.05 else 'accent'}">
        <div class="kpi-label">IC (Spearman, 全 SPY)</div>
        <div class="kpi-value">{(f'{ic_full:+.3f}' if ic_full is not None else 'n/a')}</div>
        <div class="kpi-sub">{ic_strength} alpha · 持有 IC {(f"{quant.get('ic_held'):+.3f}" if quant.get('ic_held') is not None else 'n/a')}</div>
    </div>
</div>

<h3>★ 命中 / 迴避 顯著性 (vs Random)</h3>
{bright_table_html}
<p class="narrative method-note">
    <strong>隨機期望</strong> = N × (持有檔數 {quant['n_held_in_spy']} ÷ SPY 母體 {quant['n_spy_total']});
    若模型只是隨機選股, 在 SPY Top N 中應命中此數字, Bottom N 應有 N − 該值落在持股外.
    <strong>倍數 = 實際 / 隨機期望</strong>, &gt;1 表示優於隨機 (有 alpha).
</p>

<h3>SPY 百分位分布: 持有 vs 母體</h3>
{divs['quant_edge']}

<div class="two-col">
<div>
<h3>★ 捕捉到的 SPY Top 20 漲幅股 ({len(quant['held_top20'])} 檔)</h3>
{held_top20_table_html}
</div>
<div>
<h3>★ 避開的 SPY Bottom 20 跌幅股 ({len(quant['avoided_bot20'])} 檔)</h3>
{avoided_bot20_table_html}
</div>
</div>

<details style="margin-top:18px">
<summary>期末持有且在 SPY 內的完整明細 ({quant['n_held_in_spy']} 檔, 按 SPY 百分位排序)</summary>
{his_table_html}
</details>

<div class="two-col">
<div>
<h3>SPY 內 Top 10 漲幅但組合未持有 (missed)</h3>
{missed_table_html}
</div>
<div>
<h3>SPY 內 Bottom 10 跌幅未持有 (correctly avoided)</h3>
{avoided_table_html}
</div>
</div>

<p class="method-note">
    篩選邏輯: Bloomberg 期末權重 (end_wt_port) &gt; 0 且在 SPY 母體內 (end_wt_bench &gt; 0)。
    其他 (SPY 母體外) 與期內已賣光的部位 (end_wt_port = 0) 已自動排除。
    SPY 母體百分位 rank 在 end_wt_bench &gt; 0 的 {len(quant['spy_universe'])} 檔內以 tr_bench 排序計算。
</p>
"""
    else:
        quant_edge_html = '<p>無 Bloomberg 證券層資料, 無法產生量化 Edge 分析。</p>'

    notes_html = f"""
<h3>名詞速查 (本期數值)</h3>

<dl class="glossary">

<dt>組合報酬 (自算 daily TWRR) — {fmt_pct(perf['port_return'])}</dt>
<dd>
以<strong>每日 sector MV 累乘</strong> 計算 (子期間 sector daily TWRR → Carino 跨期累乘)。<br>
口徑: 子期間內 sector return = <code>∏(1 + (MV<sub>t</sub> − MV<sub>t−1</sub> − CF<sub>t</sub>) / MV<sub>t−1</sub>) − 1</code> (CF 假設 end-of-day)<br>
本期 Bloomberg TWRR (Reference): <strong>{fmt_pct(perf.get('bb_port_return'))}</strong>, 與自算差 {fmt_pct((perf['port_return'] or 0) - (perf.get('bb_port_return') or 0))} — 詳見下方「CF timing 假設」說明。
</dd>

<dt>PORT TWRR — CF timing 假設對比</dt>
<dd>
daily TWRR 的核心爭議: 當日 CF 算在哪個時點? 三種主流假設給出不同數字 (本期同一筆資料):

<table style="border-collapse:collapse; margin:10px 0; font-size:13px;">
<thead style="background:#F5F6FA;">
<tr>
<th style="border:1px solid #DDE2E8; padding:6px 12px; text-align:left;">假設</th>
<th style="border:1px solid #DDE2E8; padding:6px 12px; text-align:left;">分母</th>
<th style="border:1px solid #DDE2E8; padding:6px 12px; text-align:right;">本期 R_P</th>
<th style="border:1px solid #DDE2E8; padding:6px 12px; text-align:left;">說明</th>
</tr>
</thead>
<tbody>
<tr style="background:#FFF8E1;">
<td style="border:1px solid #DDE2E8; padding:6px 12px;"><strong>CF at end-of-day (現行採用)</strong></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;"><code>MV<sub>t−1</sub></code></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px; text-align:right;"><strong>{fmt_pct(perf.get('twrr_cf_end'))}</strong></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;">假設 CF 在當日收盤後發生; 公式最簡, 但 CF 大時會高估</td>
</tr>
<tr>
<td style="border:1px solid #DDE2E8; padding:6px 12px;">CF at mid-day (Modified Dietz)</td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;"><code>MV<sub>t−1</sub> + 0.5×CF<sub>t</sub></code></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px; text-align:right;">{fmt_pct(perf.get('twrr_cf_middle'))}</td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;">GIPS 標準折衷; 假設 CF 在中午發生, 對買賣對稱</td>
</tr>
<tr>
<td style="border:1px solid #DDE2E8; padding:6px 12px;">CF at start-of-day</td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;"><code>MV<sub>t−1</sub> + CF<sub>t</sub></code></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px; text-align:right;">{fmt_pct(perf.get('twrr_cf_start'))}</td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;">假設 CF 在開盤前到位, 全日參與報酬; Bloomberg 風格</td>
</tr>
<tr style="background:#F8FBFE;">
<td style="border:1px solid #DDE2E8; padding:6px 12px;"><em>Bloomberg PORT TWRR (Reference)</em></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;"><em>Intraday actual prices</em></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px; text-align:right;"><em>{fmt_pct(perf.get('bb_port_return'))}</em></td>
<td style="border:1px solid #DDE2E8; padding:6px 12px;">每筆交易實際成交價分割子期間, 精度最高 (我方資料無 intraday 無法重現)</td>
</tr>
</tbody>
</table>

<strong>為何本期方法差異大?</strong> 1 月份大量買進 $569M 進入 $128M 組合, CF 是組合的 4-5 倍, 三種假設差異 ~4 pp.<br>
<strong>為何沿用 end-of-day?</strong> 公式最簡且與 Brinson sector daily TWRR 一致; 兩邊用同一口徑可保 Carino 內部 reconcile 至 ±0.5%, 不引入跨口徑混合誤差。<br>
<strong>與 Bloomberg 殘餘差距 (~8 pp)</strong>: 主要來自 Bloomberg intraday 精度 + accrued dividend 處理, 受限於我方 daily-close 資料, 無法完全消除.
</dd>

<dt>Modified Dietz (自算近似, 期間口徑) — {fmt_pct(perf.get('md_return'))}</dt>
<dd>
公式: <code>(V_end − V_start − Σ CF) / (V_start + Σ w<sub>i</sub> CF<sub>i</sub>)</code><br>
注意: 這是<strong>整期一次性 Modified Dietz</strong> (CF 按距離期初的天數加權), 與上表「CF at mid-day」的 daily 累乘版本不同。<br>
只需期初/期末 MV + 各 CF 日期金額, 簡單快速但<strong>假設報酬均勻分布</strong>。<br>
<strong>用途: 內部快速估算</strong>。
</dd>

<dt>簡單法 — {fmt_pct(perf.get('simple_return'))}</dt>
<dd>
<code>總 P&L ÷ 額度 $665M (固定分母)</code><br>
反映<strong>核給額度的資金 ROI</strong>, 不適合與 SPY 直接比較 (因 SPY 是 TWRR 口徑)。
</dd>

<dt>SPY 基準 (自算 daily TWRR) — {fmt_pct(perf['spy_return'])}</dt>
<dd>
從 Bloomberg 各期 cumulative <code>tr_bench</code> (SPY 期間 TWRR) 用 ratio 還原子期間, 再 ∏(1+r_B,t)−1 自算累乘。<br>
本期 Bloomberg TWRR (Benchmark) Reference: <strong>{fmt_pct(perf.get('bb_bench_return'))}</strong> — 兩邊應一致 (使用同一 SPY 市場數據, 不涉及 Bloomberg 歸因計算)。
</dd>

<dt>Active Return — {fmt_pct(perf['active_return'])}</dt>
<dd>
<code>Active = R<sub>P</sub> − R<sub>B</sub> = Allocation + Selection + Interaction + Residual</code><br>
本期拆解 (自算 Brinson-Fachler 3-component + Carino linking):<br>
<code>{fmt_pct(perf['industry_active'])} (Allocation) + {fmt_pct(perf['selection_active'])} (Selection) + {fmt_pct(perf.get('interaction_active'))} (Interaction) + {fmt_pct(perf.get('residual_active'))} (Residual) = <strong>{fmt_pct(perf['active_return'])}</strong></code><br>
Residual 為 Carino 多期累乘的浮點/口徑誤差, 應接近 0。
</dd>

<dt>產業配置報酬 (Allocation) — {fmt_pct(perf['industry_active'])}</dt>
<dd>
<strong>「對產業的超配/低配」相對基準產業表現帶來的純配置貢獻</strong>。<br>
公式: <code>Allocation<sub>i</sub> = (w<sub>P,i</sub> − w<sub>B,i</sub>) × (r<sub>B,i</sub> − r<sub>B,total</sub>)</code><br>
直覺: 超配 (w<sub>P</sub>&gt;w<sub>B</sub>) 一個「跑贏大盤」的產業 (r<sub>B,i</sub>&gt;r<sub>B,total</sub>) 為正貢獻; 反之為負。<br>
本期主要靠重押 IT (+38 pp 超配) 且 SPY IT 跑贏大盤; 低配 Financials/Energy 沒拖累。<br>
<strong>注意</strong>: 純配置效果不含「超配 × 個股選對」的乘積 — 該部分歸 Interaction。
</dd>

<dt>個股選擇報酬 (Selection) — {fmt_pct(perf['selection_active'])}</dt>
<dd>
<strong>「在各產業內挑的個股相對該產業平均的殘餘差異」, 以基準權重 w<sub>B</sub> 加權</strong>。<br>
公式 (classic Brinson-Fachler): <code>Selection<sub>i</sub> = w<sub>B,i</sub> × (r<sub>P,i</sub> − r<sub>B,i</sub>)</code><br>
直覺: 你在 IT 內挑的股票漲幅 r<sub>P,IT</sub> vs SPY IT 平均 r<sub>B,IT</sub> 的差, 用 SPY 中 IT 的權重 (34%) 加權。<br>
本期 +18.86%: IT 持股 (SNDK / MU / WDC 等) 大幅跑贏 SPY IT 平均, 是 Selection 的主要正貢獻來源。
</dd>

<dt>交互作用 (Interaction) — {fmt_pct(perf.get('interaction_active'))}</dt>
<dd>
<strong>「超配 × 超額」的乘積效果 — 既選對產業又選對個股的疊加紅利</strong>。<br>
公式: <code>Interaction<sub>i</sub> = (w<sub>P,i</sub> − w<sub>B,i</sub>) × (r<sub>P,i</sub> − r<sub>B,i</sub>)</code><br>
直覺: 你重押 IT (+38 pp 超配) 且 IT 持股大幅跑贏 SPY IT (+54 pp 超額), 兩者相乘產生額外貢獻。<br>
本期 +17.84%: IT 是主要來源 (超配 + 選對個股雙重效應), Bloomberg 在他們的拆解口徑會把此項併入 Factor, 我方依教科書 3-comp 獨立顯示。
</dd>

<dt>Residual (Carino 殘差) — {fmt_pct(perf.get('residual_active'))}</dt>
<dd>
<code>Residual = (R<sub>P</sub> − R<sub>B</sub>) − Σ (Allocation + Selection + Interaction)</code><br>
理論上 Carino linking 後應為 0; 實務上會有：<br>
&nbsp;&nbsp;1. 浮點累積誤差 (~0.1%)<br>
&nbsp;&nbsp;2. w<sub>B</sub> 子期間用「期末 cumulative 權重」近似 (理論上應用子期間時間平均)<br>
本期 Residual 約 +0.35%, 屬於 ±0.5% 的可接受範圍, 不影響歸因結論。
</dd>

<dt>Active Share — {fmt_pct(perf.get('active_share'), 1, sign=False)} (期末口徑)</dt>
<dd>
<code>½ × Σ |w<sub>port</sub> − w<sub>bench</sub>|</code> — 衡量組合結構與 SPY 的差異。<br>
0% = 完全複製; 100% = 完全不同; 學術 > 60% 才有 alpha 潛力, 本期屬於<strong>真主動管理</strong>。<br>
<strong>權重口徑</strong>:
<ul style="margin:6px 0">
  <li><strong>期末 end-of-period (主要顯示, {fmt_pct(perf.get('active_share'), 1, sign=False)})</strong>:
      用 5/21 持股 MV 權重 vs SPY 期末 cap weight, 反映「報告日結構差異」, 業界慣用標準。</li>
  <li>期間平均 (Reference, {fmt_pct(perf.get('active_share_avg'), 1, sign=False)}):
      用 Bloomberg <code>wt_active</code> (期間時間加權平均), 本期前段因部位逐步建立, 平均權重被攤平 → 數值偏低, 不適合作為結構差異指標。</li>
</ul>
</dd>

<dt>有效持股數 N<sub>eff</sub> — {holdings['eff_n']:.1f}</dt>
<dd>
<code>N<sub>eff</sub> = 1 ÷ Σ(w<sub>i</sub><sup>2</sup>)</code>; 等權 N 檔 → N<sub>eff</sub> = N。<br>
名義 {holdings['n_held_end']} 檔, 因集中度有效約 {holdings['eff_n']:.1f} 檔 (= 等權持有 {holdings['eff_n']:.1f} 檔的分散度)。
</dd>

</dl>
"""

    tab_contents = [
        ('overview', overview_html),
        ('contrib', contrib_html),
        ('top_holdings', top_holdings_html),
        ('sector', sector_html),
        ('attribution', attribution_html),
        ('trading', trading_html),
        ('quant_edge', quant_edge_html),
        ('notes', notes_html),
    ]
    tab_panels = '\n'.join([
        f'<section class="tab-panel{" active" if i==0 else ""}" id="tab-{tid}">{content}</section>'
        for i, (tid, content) in enumerate(tab_contents)
    ])

    css = """
* { box-sizing: border-box; }
body {
    font-family: 'Microsoft JhengHei', 'Segoe UI', Arial, sans-serif;
    margin: 0; padding: 0;
    background: #F5F6FA;
    color: #2C3E50;
    line-height: 1.6;
}
.container { max-width: 1400px; margin: 0 auto; padding: 20px; }
header.report-head {
    background: linear-gradient(135deg, #1F4E79 0%, #2C5F8F 100%);
    color: white;
    padding: 28px 24px;
    border-radius: 10px;
    margin-bottom: 20px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}
header.report-head h1 { margin: 0 0 6px 0; font-size: 26px; }
header.report-head .meta { font-size: 14px; opacity: 0.9; }

nav.tabs {
    background: white;
    padding: 6px;
    border-radius: 10px;
    margin-bottom: 16px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    position: sticky; top: 0; z-index: 10;
}
.tab-btn {
    background: transparent;
    color: #5D6D7E;
    border: none;
    padding: 9px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13.5px;
    font-family: inherit;
    font-weight: 500;
    transition: all 0.15s;
    white-space: nowrap;
}
.tab-btn:hover { background: #ECF0F1; color: #1F4E79; }
.tab-btn.active { background: #1F4E79; color: white; }

.tab-panel {
    display: none;
    background: white;
    padding: 24px;
    border-radius: 10px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    margin-bottom: 16px;
}
.tab-panel.active { display: block; animation: fadein 0.2s ease; }
@keyframes fadein { from { opacity: 0; } to { opacity: 1; } }

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 12px;
    margin: 16px 0;
}
.kpi-card {
    background: white;
    padding: 16px;
    border-radius: 6px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
    border-left: 4px solid #1F4E79;
}
.kpi-card.pos { border-left-color: #4CAF50; }
.kpi-card.neg { border-left-color: #E53935; }
.kpi-card.accent { border-left-color: #F39C12; }
.kpi-label { font-size: 11px; color: #7F8C8D; text-transform: uppercase; letter-spacing: 0.5px; }
.kpi-value { font-size: 22px; font-weight: 600; margin-top: 4px; color: #1F4E79; }
.kpi-card.pos .kpi-value { color: #4CAF50; }
.kpi-card.neg .kpi-value { color: #E53935; }
.kpi-sub { font-size: 11px; color: #95A5A6; margin-top: 2px; }

h2 { margin: 0 0 14px 0; color: #1F4E79; font-size: 20px;
     padding-bottom: 10px; border-bottom: 2px solid #ECF0F1; }
h3 { margin: 18px 0 10px 0; color: #34495E; font-size: 15px; }

.narrative {
    background: #F8F9FA;
    padding: 12px 16px;
    border-left: 4px solid #3498DB;
    margin: 14px 0 4px 0;
    border-radius: 4px;
    font-size: 14px;
}
.narrative strong { color: #1F4E79; }
.method-note { font-size: 12px; color: #7F8C8D; font-style: italic; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 900px) { .two-col { grid-template-columns: 1fr; } }

table.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin: 10px 0;
}
table.data-table th {
    background: #1F4E79; color: white; padding: 9px 8px; text-align: left;
    font-weight: 500;
}
table.data-table td { padding: 7px 8px; border-bottom: 1px solid #ECF0F1; }
table.data-table tr:hover { background: #F8F9FA; }
table.data-table td:nth-child(n+3) { text-align: right; font-variant-numeric: tabular-nums; }
table.data-table td:nth-child(2) { text-align: right; font-variant-numeric: tabular-nums; }

/* Expandable attribution table */
table.expandable-table tr.sector-row { cursor: pointer; background: #F4F8FB; }
table.expandable-table tr.sector-row:hover { background: #E3EEF7; }
table.expandable-table tr.sector-row.expanded { background: #DCE9F4; }
table.expandable-table tr.sec-detail { background: #FAFBFC; font-size: 12px; color: #5D6D7E; }
table.expandable-table tr.sec-detail td { border-bottom: 1px dashed #ECF0F1; }
table.expandable-table .toggle { display: inline-block; width: 14px; transition: transform 0.15s ease; color: #1F4E79; font-size: 11px; }
table.expandable-table tr.sector-row.expanded .toggle { transform: rotate(90deg); }

details { background: #F8F9FA; padding: 10px 14px; border-radius: 6px; margin-top: 8px; }
details summary { cursor: pointer; font-weight: 600; color: #1F4E79; }

dl.glossary dt { font-weight: 600; color: #1F4E79; margin-top: 14px; }
dl.glossary dd { margin-left: 0; padding: 6px 0 0 12px; border-left: 3px solid #ECF0F1; color: #34495E; }

footer { text-align: center; padding: 20px; color: #95A5A6; font-size: 12px; }
"""

    js = """
document.addEventListener('DOMContentLoaded', function() {
  var buttons = document.querySelectorAll('.tab-btn');
  var panels = document.querySelectorAll('.tab-panel');
  buttons.forEach(function(btn) {
    btn.addEventListener('click', function() {
      var target = btn.getAttribute('data-target');
      buttons.forEach(function(b) { b.classList.remove('active'); });
      panels.forEach(function(p) { p.classList.remove('active'); });
      btn.classList.add('active');
      document.getElementById('tab-' + target).classList.add('active');
      // Trigger plotly resize for charts in the newly shown tab
      setTimeout(function() {
        if (window.Plotly) {
          document.querySelectorAll('#tab-' + target + ' .plotly-graph-div').forEach(function(el) {
            Plotly.Plots.resize(el);
          });
        }
      }, 50);
    });
  });

  // Expandable attribution table: sector row toggles its security rows
  // 範圍限制在同一個 table 內, 避免多張表互相觸發
  document.querySelectorAll('table.expandable-table tr.sector-row').forEach(function(row) {
    row.addEventListener('click', function() {
      var sector = this.getAttribute('data-sector');
      var expanded = this.classList.toggle('expanded');
      var table = this.closest('table');
      var sel = 'tr.sec-detail[data-parent="' + sector + '"]';
      table.querySelectorAll(sel).forEach(function(r) {
        r.style.display = expanded ? '' : 'none';
      });
    });
  });
});
"""

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>計量持股組合分析報告</title>
{plotly_cdn}
<style>{css}</style>
</head>
<body>
<div class="container">

<header class="report-head">
    <h1>計量持股組合分析報告</h1>
    <div class="meta">
        基準: SPY (S&P 500 ETF) ·
        產出: {datetime.now():%Y-%m-%d %H:%M} ·
        資料: 計量績效分析.xlsx
    </div>
</header>

<nav class="tabs">
{tab_buttons}
</nav>

{tab_panels}

<footer>
    portfolio_analysis.py + generate_html_report.py · Plotly {__import__('plotly').__version__} · 資料來源: shared-from-console
</footer>

</div>
<script>{js}</script>
</body>
</html>
"""
    return html


def main():
    print('Loading data...')
    d = load_data(FP)
    print('Building HTML report (standalone)...')
    # 為 standalone 重算 results
    from portfolio_analysis import (section_performance, section_holdings, section_daily,
                                     section_sector, section_attribution, section_trades,
                                     section_quant_edge)
    results = {}
    results['perf'] = section_performance(d)
    results['holdings'] = section_holdings(d)
    results['daily'] = section_daily(d)
    results['sector_df'] = section_sector(d)
    results['attribution'] = section_attribution(d)
    results['trades'] = section_trades(d)
    results['quant'] = section_quant_edge(d)
    html = build_html(d, results)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'✓ Saved: {OUTPUT_HTML}')
    print(f'  Size: {OUTPUT_HTML.stat().st_size / 1024:.1f} KB')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
