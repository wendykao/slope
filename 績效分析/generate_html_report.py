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


# =============================================================
# Chart builders
# =============================================================
def chart_return_bar(perf):
    labels = ['簡單法', 'Modified Dietz', 'Bloomberg TWRR', 'SPY (基準)', 'Active Return']
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


def chart_contributors(top10, bot10):
    df = pd.concat([top10, bot10], ignore_index=True)
    df = df.sort_values('TOTAL_PL', ascending=True)
    colors = [COLOR_POS if v > 0 else COLOR_NEG for v in df['TOTAL_PL']]
    fig = go.Figure(go.Bar(
        y=df['ticker'] + ' ' + df['STK_NAME'].fillna('').astype(str).str[:14],
        x=df['TOTAL_PL']/1e6,
        orientation='h',
        marker_color=colors,
        text=[f'${v/1e6:+,.2f}M' for v in df['TOTAL_PL']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>P&L: $%{x:+,.2f}M<extra></extra>',
    ))
    pnl_max = df['TOTAL_PL'].max() / 1e6
    pnl_min = df['TOTAL_PL'].min() / 1e6
    pad = max(abs(pnl_max), abs(pnl_min)) * 0.2
    fig.update_layout(
        title='Top 10 / Bottom 10 P&L 貢獻者 (期末快照)',
        xaxis_title='P&L (USD 百萬)',
        xaxis_range=[pnl_min - pad, pnl_max + pad],
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=620,
        showlegend=False, margin=dict(t=60, b=40, l=200, r=80),
    )
    return fig


def chart_top_holdings(held_sorted):
    top = held_sorted.head(15).copy().iloc[::-1]
    fig = go.Figure(go.Bar(
        y=top['ticker'] + ' ' + top['STK_NAME'].fillna('').astype(str).str[:12],
        x=top['weight']*100,
        orientation='h',
        marker_color=COLOR_PORT,
        text=[f'{v*100:.2f}%' for v in top['weight']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>權重: %{x:.2f}%<br>MV: $%{customdata:,.0f}<extra></extra>',
        customdata=top['TOTAL_MV'],
    ))
    max_w = top['weight'].max() * 100
    fig.update_layout(
        title='Top 15 持股權重 (期末)',
        xaxis_title='權重', xaxis_ticksuffix='%',
        xaxis_range=[0, max_w * 1.18],
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=560,
        showlegend=False, margin=dict(t=60, b=40, l=200, r=80),
    )
    return fig


def chart_sector_exposure(sector_df):
    df = sector_df.copy().sort_values('wt_active', ascending=True)
    # 補英文 sector 標籤
    df['sector_label'] = df['name'].map(lambda x: f"{x} / {SECTOR_EN_MAP.get(x, '')}")

    fig = make_subplots(rows=1, cols=2, subplot_titles=['平均權重: 組合 vs SPY (Bloomberg)', '主動權重 (Active Weight)'],
                        horizontal_spacing=0.18)

    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['wt_port'].fillna(0),
        orientation='h', name='組合', marker_color=COLOR_PORT,
        text=[f'{v:.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['wt_port'].fillna(0)],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>組合: %{x:.2f}%<extra></extra>',
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['wt_bench'].fillna(0),
        orientation='h', name='SPY', marker_color=COLOR_BENCH, opacity=0.7,
        text=[f'{v:.1f}%' if pd.notna(v) and v != 0 else '0.0%' for v in df['wt_bench'].fillna(0)],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>SPY: %{x:.2f}%<extra></extra>',
    ), row=1, col=1)

    colors = [COLOR_POS if v > 0 else COLOR_NEG for v in df['wt_active'].fillna(0)]
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['wt_active'].fillna(0),
        orientation='h', marker_color=colors, showlegend=False,
        text=[f'{v:+.1f}%' for v in df['wt_active'].fillna(0)],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>Active: %{x:+.2f}%<extra></extra>',
    ), row=1, col=2)

    max_w = df[['wt_port', 'wt_bench']].max().max()
    aw_min = df['wt_active'].min()
    aw_max = df['wt_active'].max()
    aw_pad = max(abs(aw_min), abs(aw_max)) * 0.25
    fig.update_xaxes(ticksuffix='%', row=1, col=1, range=[0, max_w * 1.18])
    fig.update_xaxes(ticksuffix='%', row=1, col=2, range=[aw_min - aw_pad, aw_max + aw_pad])
    fig.update_layout(
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=540,
        barmode='group', margin=dict(t=80, b=40, l=220, r=60),
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
    # Active = 產業報酬 + 個股選擇 (Timing 已於資料載入時移除)
    items = [
        ('產業報酬', perf['industry_active'] or 0),
        ('個股選擇報酬', perf['selection_active'] or 0),
        ('Active Return', perf['active_return'] or 0),
    ]
    measures = ['relative', 'relative', 'total']
    fig = go.Figure(go.Waterfall(
        name='Bloomberg Attribution',
        orientation='v',
        measure=measures,
        x=[i[0] for i in items],
        y=[i[1]*100 for i in items],
        text=[f'{v*100:+.2f}%' for _, v in items],
        textposition='outside',
        connector={'line': {'color': '#BDBDBD'}},
        increasing={'marker': {'color': COLOR_POS}},
        decreasing={'marker': {'color': COLOR_NEG}},
        totals={'marker': {'color': COLOR_PORT}},
    ))
    fig.update_layout(
        title='Bloomberg 歸因瀑布圖 (Active Return 拆解)',
        yaxis_title='貢獻 (%)', yaxis_ticksuffix='%',
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=480,
        margin=dict(t=60, b=60, l=60, r=40),
    )
    return fig


def chart_sector_attribution(sector_df):
    df = sector_df.copy().sort_values('ctr_active', ascending=True)
    df['sector_label'] = df['name'].map(lambda x: f"{x} / {SECTOR_EN_MAP.get(x, '')}")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['industry_active'].fillna(0),
        orientation='h', name='產業報酬',
        marker_color=COLOR_INFO,
        hovertemplate='%{y}<br>產業報酬: %{x:+.3f}%<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=df['sector_label'], x=df['sel_active'].fillna(0),
        orientation='h', name='個股選擇報酬',
        marker_color=COLOR_ACCENT,
        hovertemplate='%{y}<br>個股選擇: %{x:+.3f}%<extra></extra>',
    ))
    fig.update_layout(
        title='各 Sector Bloomberg 歸因細項 (% 對 Active Return 的貢獻)',
        xaxis_title='貢獻 (%)', xaxis_ticksuffix='%',
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
    """在倉持股於 SPY 母體的 YTD% 百分位 — 水平條"""
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
        title=f"在倉持股於 SPY 母體 YTD% 百分位分布 (平均 {quant['mean_pct']:.1f}, 越高越好)",
        xaxis_title='SPY 百分位 (越高 = YTD 表現越好)',
        xaxis_range=[0, 108],
        template=PLOTLY_TEMPLATE, font=CHART_FONT,
        height=max(400, 22 * len(df) + 100),
        showlegend=False,
        margin=dict(t=90, b=40, l=180, r=80),
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
    sector_df = results['sector_df']
    trades_data = results['trades']
    quant = results.get('quant')

    # Build all figures
    figs = {
        'return': chart_return_bar(perf),
        'mv': chart_daily_mv(daily, perf),
        'pnl_ts': chart_daily_pnl(daily),
        'contrib': chart_contributors(holdings['top10'], holdings['bot10']),
        'top_holdings': chart_top_holdings(holdings['held_sorted']),
        'sector': chart_sector_exposure(sector_df),
        'sector_returns': chart_sector_returns(sector_df),
        'waterfall': chart_attribution_waterfall(perf),
        'sec_attr': chart_sector_attribution(sector_df),
        'monthly': chart_monthly_trades(trades_data),
        'winrate': chart_winrate(trades_data),
    }
    if quant is not None:
        figs['quant_edge'] = chart_spy_percentile(quant)

    # Render chart divs
    divs = {k: fig_to_div(v, f'fig_{k}') for k, v in figs.items()}

    # Tables
    top10_disp = holdings['top10'].copy()
    top10_disp.columns = ['Ticker', '公司', 'URCG', 'RCG', 'DVD', 'P&L']
    for c in ['URCG', 'RCG', 'DVD', 'P&L']:
        top10_disp[c] = top10_disp[c].apply(lambda x: fmt_usd(x, 0))
    top10_html = df_to_html_table(top10_disp)

    bot10_disp = holdings['bot10'].copy()
    bot10_disp.columns = ['Ticker', '公司', 'URCG', 'RCG', 'DVD', 'P&L']
    for c in ['URCG', 'RCG', 'DVD', 'P&L']:
        bot10_disp[c] = bot10_disp[c].apply(lambda x: fmt_usd(x, 0))
    bot10_html = df_to_html_table(bot10_disp)

    sec_attr_table = sector_df[['name', 'wt_port', 'wt_bench', 'wt_active',
                                 'tr_port', 'tr_bench', 'tr_active',
                                 'industry_active', 'sel_active', 'ctr_active']].copy()
    sec_attr_table.columns = ['Sector', 'wP%', 'wB%', 'wActive%', 'rP%', 'rB%', 'rActive%',
                                '產業報酬', '個股選擇', 'CTR']
    for c in sec_attr_table.columns[1:]:
        sec_attr_table[c] = sec_attr_table[c].apply(lambda x: f'{x:+.3f}' if pd.notna(x) else 'n/a')
    sec_attr_table_html = df_to_html_table(sec_attr_table)

    # End holdings full table
    end_holdings = holdings['held_sorted'][['ticker', 'STK_NAME', 'TOTAL_SHARES', 'TOTAL_COST',
                                              'TOTAL_MV', 'TOTAL_PL', 'weight']].copy()
    end_holdings.columns = ['Ticker', '公司', '股數', '庫存成本', '市值', 'P&L', '權重']
    end_holdings['股數'] = end_holdings['股數'].apply(lambda x: f'{int(x):,}')
    for c in ['庫存成本', '市值', 'P&L']:
        end_holdings[c] = end_holdings[c].apply(lambda x: fmt_usd(x, 0))
    end_holdings['權重'] = end_holdings['權重'].apply(lambda x: fmt_pct(x, 2, sign=False))
    end_holdings_html = df_to_html_table(end_holdings)

    period_start = perf['period_start']
    period_end = perf['period_end']

    # Tabs definition
    tabs = [
        ('overview', '0. 概覽'),
        ('daily', '1. 每日走勢'),
        ('contrib', '2. 貢獻者'),
        ('top_holdings', '3. Top 持股'),
        ('sector', '4. 產業曝險'),
        ('attribution', '5. Bloomberg 歸因'),
        ('trading', '6. 交易分析'),
        ('quant_edge', '7. 量化 Edge'),
        ('notes', '8. 說明'),
    ]

    plotly_cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'

    tab_buttons = '\n'.join([
        f'<button class="tab-btn{" active" if i==0 else ""}" data-target="{tid}">{label}</button>'
        for i, (tid, label) in enumerate(tabs)
    ])

    # ----- Content for each tab -----
    overview_html = f"""
<h2>{period_start.date()} → {period_end.date()} ({perf['days']} 天) · 基準: SPY</h2>
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">組合報酬 (Bloomberg TWRR)</div>
        <div class="kpi-value">{fmt_pct(perf['port_return'])}</div>
        <div class="kpi-sub">每日真實時間加權</div>
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
        <div class="kpi-label">Active Share</div>
        <div class="kpi-value">{fmt_pct(perf.get('active_share'), 1, sign=False)}</div>
        <div class="kpi-sub">vs SPY 結構差異</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">產業報酬</div>
        <div class="kpi-value">{fmt_pct(perf['industry_active'])}</div>
        <div class="kpi-sub">Allocation 對 Active 貢獻</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">個股選擇報酬</div>
        <div class="kpi-value">{fmt_pct(perf['selection_active'])}</div>
        <div class="kpi-sub">Selection 對 Active 貢獻</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期末 MV</div>
        <div class="kpi-value">{fmt_usd_m(perf['mv_end'], 0)}</div>
        <div class="kpi-sub">{period_end.date()}</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期末庫存成本</div>
        <div class="kpi-value">{fmt_usd_m(perf['cost_end'], 0)}</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期末總 P&L</div>
        <div class="kpi-value">{fmt_usd_m(perf['pnl_end'], 0)}</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期末持倉</div>
        <div class="kpi-value">{holdings['n_held_end']}</div>
        <div class="kpi-sub">期初: {holdings['n_held_start']}</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">有效持股數</div>
        <div class="kpi-value">{holdings['eff_n']:.1f}</div>
        <div class="kpi-sub">1/Σw²</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期內交易</div>
        <div class="kpi-value">{trades_data['n_buys'] + trades_data['n_sells']}</div>
        <div class="kpi-sub">買 {trades_data['n_buys']} / 賣 {trades_data['n_sells']}</div>
    </div>
</div>

<h3>報酬率比較 (各口徑)</h3>
{divs['return']}

<p class="narrative">
    Bloomberg TWRR (組合) <strong>{fmt_pct(perf['port_return'])}</strong> vs SPY {fmt_pct(perf['spy_return'])}
    = Active <strong>{fmt_pct(perf['active_return'])}</strong>;
    Modified Dietz 自算 {fmt_pct(perf.get('md_return'))} 接近 Bloomberg TWRR (應略有差異, MD 是近似法).
    Active Share <strong>{fmt_pct(perf.get('active_share'), 1, sign=False)}</strong> 表示組合裡有此比例的權重與 SPY 配置不同。
    Active 分解: 產業報酬 {fmt_pct(perf['industry_active'])} + 個股選擇 {fmt_pct(perf['selection_active'])}。
</p>
"""

    daily_html = f"""
<h3>每日市值 vs 庫存成本</h3>
{divs['mv']}
<p class="narrative">
    每日市值 (TOTAL_MV, 藍實線) 與庫存成本 (TOTAL_COST, 橘虛線) 走勢。
    期間 cost 從 {fmt_usd_m(perf['cost_start'], 1)} 變為 {fmt_usd_m(perf['cost_end'], 0)};
    MV 從 {fmt_usd_m(perf['mv_start'], 1)} 變為 {fmt_usd_m(perf['mv_end'], 0)}。
    MV 高於 Cost 的部分即為未實現損益 URCG。
</p>

<h3>累積 P&L 時間序列</h3>
{divs['pnl_ts']}
<p class="narrative">
    累積總 P&L (含未實現+已實現+股息) 隨時間演變; 同時疊上未實現損益 (URCG) 與持倉檔數 (右軸)。
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

<div class="two-col">
    <div><h3>Top 10 (期末)</h3>{top10_html}</div>
    <div><h3>Bottom 10 (期末)</h3>{bot10_html}</div>
</div>
"""

    top_holdings_html = f"""
{divs['top_holdings']}
<p class="narrative">
    最大持股 <strong>{top1_ticker}</strong> 權重 {fmt_pct(top1.get('weight', 0), 2, sign=False)};
    Top 10 集中 <strong>{fmt_pct(holdings['top10_w'], 1, sign=False)}</strong>;
    有效持股數 {holdings['eff_n']:.1f} (1/Σw²)。
</p>
<details style="margin-top:18px"><summary>完整在倉部位明細 ({len(holdings['held_sorted'])} 檔)</summary>
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
    sector_html = f"""
<h3>產業權重曝險</h3>
{divs['sector']}
<p class="narrative">
    <strong>顯著超配</strong>: {over_desc}<br>
    <strong>顯著低配</strong>: {under_desc}
</p>

<h3>產業報酬率</h3>
{divs['sector_returns']}
<p class="narrative">
    <strong>本組合表現最強 sector</strong>: {best_r['name']} rP {best_r['tr_port']:+.2f}% vs SPY {best_r['tr_bench']:+.2f}% (Active {best_r['tr_active']:+.2f}%)<br>
    <strong>本組合表現最弱 sector</strong>: {worst_r['name']} rP {worst_r['tr_port']:+.2f}% vs SPY {worst_r['tr_bench']:+.2f}% (Active {worst_r['tr_active']:+.2f}%)
</p>
"""

    attribution_html = f"""
<h3>Active Return 拆解瀑布圖</h3>
{divs['waterfall']}
<p class="narrative">
    Bloomberg 歸因把 Active Return 拆成兩個來源:<br>
    <strong>產業報酬 ({fmt_pct(perf['industry_active'])})</strong>:
    產業權重 vs SPY 的差異所產生的貢獻 (Allocation/Factor 效果)。<br>
    <strong>個股選擇報酬 ({fmt_pct(perf['selection_active'])})</strong>:
    在各產業內個股選擇的效果 (Stock Selection)。
</p>

<h3>各 Sector 細項拆解</h3>
{divs['sec_attr']}
<h3>各 Sector 細項表</h3>
{sec_attr_table_html}
<p class="narrative method-note">
    產業報酬 / 個股選擇 兩項對應每個 sector 對 Active 的拆解貢獻 (% 點)。
    CTR (Contribution to Return) = 該 sector 對 Active 的總貢獻 ≈ 產業報酬 + 個股選擇。
</p>
"""

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

    trading_html = f"""
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

<h3>賣出單筆勝率</h3>
{divs['winrate']}
<div class="kpi-grid">
    <div class="kpi-card {('pos' if win_rate>=50 else 'neg')}">
        <div class="kpi-label">勝率</div>
        <div class="kpi-value">{win_rate:.1f}%</div>
        <div class="kpi-sub">{trades_data['n_winners']} 獲利 / {n_sp} 賣出</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">平均獲利 / 筆</div>
        <div class="kpi-value">{fmt_usd(trades_data['avg_win'])}</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">平均虧損 / 筆</div>
        <div class="kpi-value">{fmt_usd(trades_data['avg_loss'])}</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">獲利/虧損比</div>
        <div class="kpi-value">{f'{pl_ratio:.2f}x' if pl_ratio else 'n/a'}</div>
    </div>
    <div class="kpi-card {('pos' if trades_data['realized_sum']>=0 else 'neg')}">
        <div class="kpi-label">合計實現損益</div>
        <div class="kpi-value">{fmt_usd_m(trades_data['realized_sum'], 2)}</div>
    </div>
</div>
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

        quant_edge_html = f"""
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">在倉 ∩ SPY 命中率</div>
        <div class="kpi-value">{fmt_pct(quant['hit_rate'], 1)}</div>
        <div class="kpi-sub">{quant['n_profitable_in_spy']} 獲利 / {quant['n_held_in_spy']} 檔</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">平均 SPY 百分位</div>
        <div class="kpi-value">{quant['mean_pct']:.1f}</div>
        <div class="kpi-sub">50 = 隨機選股</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">落在 SPY 前 1/4</div>
        <div class="kpi-value">{quant['pct_top25']:.1f}%</div>
        <div class="kpi-sub">SPY 百分位 &gt; 75</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">落在 SPY 上半</div>
        <div class="kpi-value">{quant['pct_above_50']:.1f}%</div>
        <div class="kpi-sub">SPY 百分位 &gt; 50</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">持股等權平均 YTD</div>
        <div class="kpi-value">{quant['port_avg_uw']:+.2f}%</div>
        <div class="kpi-sub">vs SPY 等權 {quant['spy_avg_uw']:+.2f}%</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">超額 (等權)</div>
        <div class="kpi-value">{(quant['port_avg_uw'] - quant['spy_avg_uw']):+.2f}%</div>
        <div class="kpi-sub">持股 SPY 部分 − SPY 母體</div>
    </div>
</div>

<h3>★ 量化模型亮點: 強漲股捕捉率 / 弱跌股迴避率</h3>
<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">SPY Top 10 漲幅 命中</div>
        <div class="kpi-value">{bright[10]['held_in_top']} / 10</div>
        <div class="kpi-sub">捕捉到強漲股</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">SPY Top 20 漲幅 命中</div>
        <div class="kpi-value">{bright[20]['held_in_top']} / 20</div>
        <div class="kpi-sub">捕捉到強漲股</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">SPY Top 50 漲幅 命中</div>
        <div class="kpi-value">{bright[50]['held_in_top']} / 50</div>
        <div class="kpi-sub">捕捉到強漲股</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">SPY Bottom 10 跌幅 迴避</div>
        <div class="kpi-value">{bright[10]['avoided_bottom']} / 10</div>
        <div class="kpi-sub">避開弱跌股</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">SPY Bottom 20 跌幅 迴避</div>
        <div class="kpi-value">{bright[20]['avoided_bottom']} / 20</div>
        <div class="kpi-sub">避開弱跌股</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">SPY Bottom 50 跌幅 迴避</div>
        <div class="kpi-value">{bright[50]['avoided_bottom']} / 50</div>
        <div class="kpi-sub">避開弱跌股</div>
    </div>
</div>

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

<h3>SPY 百分位分布圖 (所有在倉 ∩ SPY 部位)</h3>
{divs['quant_edge']}

<p class="narrative">
    投組平均權重 &gt; 0 且在 SPY 母體內共 <strong>{quant['n_held_in_spy']}</strong> 檔,
    平均落在母體 <strong>{quant['mean_pct']:.1f}</strong> 百分位 (50 = 隨機選股),
    前 1/4 (百分位 &gt; 75) 占 <strong>{quant['pct_top25']:.1f}%</strong>。
    持股 SPY 部分等權平均 YTD {quant['port_avg_uw']:+.2f}% vs SPY 母體等權平均 {quant['spy_avg_uw']:+.2f}%
    = 超額 <strong>{(quant['port_avg_uw'] - quant['spy_avg_uw']):+.2f}%</strong>。<br>
    <strong>亮點</strong>: 持有 SPY Top 10 漲幅中的 <strong>{bright[10]['held_in_top']}</strong> 檔 ·
    Top 20 中的 <strong>{bright[20]['held_in_top']}</strong> 檔 ·
    避開 Bottom 10 跌幅 <strong>{bright[10]['avoided_bottom']}/10</strong> ·
    避開 Bottom 20 跌幅 <strong>{bright[20]['avoided_bottom']}/20</strong>。
</p>

<h3>投組平均權重&gt;0 且在 SPY 內的完整明細, 按 SPY 百分位排序</h3>
{his_table_html}

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
    篩選邏輯: Bloomberg 投組平均權重 (wt_port) &gt; 0 且在 SPY 母體內 (wt_bench &gt; 0)。
    Off-Benchmark (wt_bench=0/NaN) 已自動排除。注意 wt_port 為期間平均, 包含期內曾持有但已賣出的部位。
    SPY 母體百分位 rank 在 wt_bench &gt; 0 的 {len(quant['spy_universe'])} 檔內以 tr_bench 排序計算。
</p>
"""
    else:
        quant_edge_html = '<p>無 Bloomberg 證券層資料, 無法產生量化 Edge 分析。</p>'

    notes_html = f"""
<h3>名詞解釋</h3>
<dl class="glossary">

<dt>組合報酬 (Bloomberg TWRR) — 本期 {fmt_pct(perf['port_return'])}</dt>
<dd>
<strong>Time-Weighted Rate of Return (時間加權報酬率, 每日複利版)</strong>:<br>
<code>R = ∏ (1 + r_i) − 1</code>, 其中 r_i 為每日報酬, <code>r_i = (MV_i − MV_{{i−1}} − CF_i) / MV_{{i−1}}</code><br>
逐日計算當日報酬率 (扣除當日 cash flow), 然後把所有日報酬複利相乘。<br>
<strong>特性</strong>:
<ul>
  <li>需要逐日 MV 資料 (Bloomberg 有 daily snapshot)</li>
  <li>真實反映「報酬路徑」(每日波動都納入)</li>
  <li>完全扣除 cash flow 時點影響 — 不會因「期初買大量」而扭曲</li>
  <li>業界績效比較的標準口徑 (GIPS 規範採用)</li>
</ul>
</dd>

<dt>Modified Dietz (自算近似) — 本期 {fmt_pct(perf.get('md_return'))}</dt>
<dd>
<strong>Modified Dietz 公式 (TWRR 的單期近似)</strong>:<br>
<code>R = (V_end − V_start − Σ CF) / (V_start + Σ w_i · CF_i)</code><br>
其中 w_i = (T − t_i) / T, T 為期間總天數, t_i 為第 i 筆 CF 距期初天數。<br>
分子: 期末 MV − 期初 MV − 期內所有現金流, 即「純粹由報酬產生的 MV 變動」<br>
分母: 期初 MV + 各 CF 按時間加權後的金額, 即「期間平均運用資金」<br>
<strong>特性</strong>:
<ul>
  <li>只需要期初、期末 MV + 各 CF 的金額與日期</li>
  <li>每筆 CF 視為對「stock-portfolio 帳戶」的外部現金流</li>
  <li>假設報酬在期間內呈線性/均勻 — 是 TWRR 的<strong>單期近似</strong></li>
  <li>無法捕捉期內 MV 的非線性波動</li>
</ul>
</dd>

<dt>兩者差異與本期數據解讀</dt>
<dd>
<table class="data-table" style="margin-top:6px;">
<tr><th></th><th>Bloomberg TWRR</th><th>Modified Dietz</th></tr>
<tr><td>資料需求</td><td>每日 MV (108 天 snapshot)</td><td>期初/期末 MV + 各 CF 日期金額</td></tr>
<tr><td>計算方式</td><td>每日報酬複利</td><td>單期分子÷時間加權分母</td></tr>
<tr><td>對波動敏感度</td><td>完整捕捉路徑波動</td><td>假設均勻成長, 對波動不敏感</td></tr>
<tr><td>準確度</td><td>精確 (業界標準)</td><td>近似 (CF 集中且波動大時誤差較大)</td></tr>
<tr><td>本期結果</td><td><strong>{fmt_pct(perf['port_return'])}</strong></td><td>{fmt_pct(perf.get('md_return'))}</td></tr>
</table>
<br>
<strong>為何本期兩者相差約 {fmt_pct((perf.get('md_return') or 0) - perf['port_return'])}?</strong><br>
1 月集中部署 $631M (期初一次到位), 之後組合 MV 一路上漲至 5 月初峰值約 $1B, 然後 5 月中下旬回落至期末 $683M (peak-to-end 回吐約 32%)。
Modified Dietz 假設報酬均勻分散在 150 天, 因此忽略了「峰值在 5 月初、之後回吐」的非線性路徑;
Bloomberg TWRR 逐日複利, 完整反映此波動 — 因此 MD 略<strong>高估</strong>真實時間加權績效。<br>
實務上以 <strong>Bloomberg TWRR 為準</strong>; Modified Dietz 為內部快速試算的近似法, 兩者通常相差 0.5~3 pp 視 CF 集中度與波動而定。
</dd>

<dt>Active Share — 本期 {fmt_pct(perf.get('active_share'), 1, sign=False)}</dt>
<dd>Active Share = ½ × Σ |w_port,i − w_bench,i|。0% = 完全複製指數, 100% = 跟指數完全不同。本報告由 Bloomberg "活絡權重" 推算: 平均 |活絡| / 2。 學術: > 60% 才有 alpha 潛力; > 80% 為高度主動。</dd>

<dt>有效持股數 N_eff — 本期 {holdings['eff_n']:.1f}</dt>
<dd>N_eff = 1 / Σ(w_i²)。等權 N 檔 → N_eff = N; 越集中, N_eff 越小。反映「實質有多少檔在影響組合」。</dd>

<dt>產業報酬 / 個股選擇報酬 (Bloomberg)</dt>
<dd>
<strong>產業報酬 (原 Factor)</strong>: 產業 (或風格因子) 配置 vs 基準的差異產生的報酬, 相當於 Brinson Allocation 效果。本期 <strong>{fmt_pct(perf['industry_active'])}</strong><br>
<strong>個股選擇報酬 (原 Selection)</strong>: 在各產業內個股選擇能力的貢獻 (Stock Selection)。本期 <strong>{fmt_pct(perf['selection_active'])}</strong>
</dd>
</dl>
"""

    tab_contents = [
        ('overview', overview_html),
        ('daily', daily_html),
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
        分析期間: {period_start.date()} ~ {period_end.date()} ({perf['days']} 天) ·
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
