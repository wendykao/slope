# -*- coding: utf-8 -*-
"""
產生 HTML 績效分析報告 (含 Plotly 互動圖表)
輸出檔: 績效分析報告.html (自含 plotly.js, 可離線開啟)
"""
import sys, io
from pathlib import Path
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
from plotly.subplots import make_subplots
from datetime import datetime

# Import data loader from main analysis module
sys.path.insert(0, str(Path(__file__).parent))
from portfolio_analysis import load_data, FP, PERIOD_START, PERIOD_END, DAYS

OUTPUT_HTML = Path(__file__).parent / '績效分析報告.html'

# ===== Color palette =====
COLOR_PORT = '#1F4E79'
COLOR_BENCH = '#A6A6A6'
COLOR_POS = '#4CAF50'
COLOR_NEG = '#E53935'
COLOR_ACCENT = '#F39C12'
COLOR_INFO = '#3498DB'
PLOTLY_TEMPLATE = 'plotly_white'

CHART_FONT = dict(family='Microsoft JhengHei, Arial, sans-serif', size=12)


def fmt_pct(x, digits=2, sign=True):
    if pd.isna(x):
        return 'n/a'
    s = f'{x*100:+.{digits}f}%' if sign else f'{x*100:.{digits}f}%'
    return s


def fmt_usd(x, digits=0):
    if pd.isna(x):
        return 'n/a'
    return f'${x:,.{digits}f}'


def fmt_int(x):
    if pd.isna(x):
        return 'n/a'
    return f'{int(x):,}'


# =============================================================
# Computations
# =============================================================
def compute_performance(d):
    t = d['totals']
    tr = d['tr']
    spy = d['spy']

    cfs = tr.copy()
    cfs['days_since_start'] = (cfs['交易日期'] - PERIOD_START).dt.days
    cfs['weight'] = (DAYS - cfs['days_since_start']) / DAYS
    cfs['weighted_cf'] = cfs['signed_amount'] * cfs['weight']

    net_cf = cfs['signed_amount'].sum()
    weighted_cf = cfs['weighted_cf'].sum()
    denom = t['mv_start'] + weighted_cf
    md_return = (t['mv_end'] - t['mv_start'] - net_cf) / denom
    simple_return = t['pnl_end'] / t['cost_end']

    # SPY return: 用 Bloomberg 實際 YTD (不再用 cap-weighted 自算, 因期間 SPY 成分股調整)
    spy_tr = t['spy_ytd']

    return {
        'simple_return': simple_return,
        'md_return': md_return,
        'spy_return': spy_tr,
        'active_simple': simple_return - spy_tr,
        'active_md': md_return - spy_tr,
        'net_cf': net_cf,
        'mv_start': t['mv_start'],
        'mv_end': t['mv_end'],
        'cost_end': t['cost_end'],
        'pnl_end': t['pnl_end'],
        'active_share': t['active_share'],
        'overlap': t['index_overlap_weight'],
    }


def compute_pnl_decomp(d):
    h5 = d['h5']
    return {
        'urcg': h5['URCG(YTD)'].sum(),
        'rcg': h5['RCG(YTD)'].sum(),
        'dvd': h5['DVD(YTD)'].sum(),
        'total': h5['P&L(YTD)'].sum(),
    }


def compute_holdings(d):
    h5 = d['h5']
    h12 = d['h12']
    held = h5[h5['市值'] > 0].copy()

    top10 = h5.nlargest(10, 'P&L(YTD)')[['ticker', '公司名稱', 'URCG(YTD)', 'RCG(YTD)', 'DVD(YTD)', 'P&L(YTD)', '損益貢獻%']].copy()
    bot10 = h5.nsmallest(10, 'P&L(YTD)')[['ticker', '公司名稱', 'URCG(YTD)', 'RCG(YTD)', 'DVD(YTD)', 'P&L(YTD)', '損益貢獻%']].copy()

    held_sorted = held.sort_values('權重', ascending=False)

    set_5 = set(held['ticker'])
    set_12 = set(h12[h12['市值'] > 0]['ticker'])

    return {
        'held': held,
        'held_sorted': held_sorted,
        'top10': top10,
        'bot10': bot10,
        'n_continued': len(set_12 & set_5),
        'n_new': len(set_5 - set_12),
        'n_sold_out': len(set_12 - set_5),
        'n_held_5': len(set_5),
        'n_held_12': len(set_12),
        'top5_w': held_sorted.head(5)['權重'].sum(),
        'top10_w': held_sorted.head(10)['權重'].sum(),
        'hhi': (held['權重'] ** 2).sum() * 10000,
        'eff_n': 1 / (held['權重'] ** 2).sum(),
    }


def compute_sector(d):
    h5 = d['h5']
    spy = d['spy']

    all_pos = h5.merge(spy[['ticker', 'sector']], on='ticker', how='left')
    all_pos['sector'] = all_pos['sector'].fillna('Off-Benchmark')

    held = all_pos[all_pos['市值'] > 0]
    port_mv_total = held['市值'].sum()
    port_cost_total = held['庫存成本(YTD)'].sum()

    port_sector = all_pos.groupby('sector').agg(
        mv=('市值', 'sum'),
        cost=('庫存成本(YTD)', 'sum'),
        pnl=('P&L(YTD)', 'sum'),
        n=('ticker', 'count')
    ).reset_index()
    port_sector['port_w_mv'] = port_sector['mv'] / port_mv_total
    port_sector['port_w'] = port_sector['cost'] / port_cost_total
    port_sector['port_r'] = port_sector['pnl'] / port_sector['cost'].replace(0, np.nan)

    spy_clean = spy.dropna(subset=['spy_weight'])
    spy_sector = spy_clean.groupby('sector').apply(
        lambda g: pd.Series({
            'bench_w': g['spy_weight'].sum(),
            'bench_r': (g['spy_weight'] * g['tr_ytd']).sum() / g['spy_weight'].sum(),
        }), include_groups=False
    ).reset_index()

    merged = port_sector.merge(spy_sector, on='sector', how='outer').fillna({'port_w_mv': 0, 'port_w': 0, 'bench_w': 0, 'bench_r': 0, 'pnl': 0, 'cost': 0, 'mv': 0, 'n': 0})
    merged['active_w_mv'] = merged['port_w_mv'] - merged['bench_w']
    return merged.sort_values('active_w_mv', ascending=False)


def compute_attribution(sector_table, spy_full, cost_total, spy_actual_ytd):
    df = sector_table.copy()
    # r_b_total 用 SPY 實際 YTD (Bloomberg), 不再用 cap-weighted 自算
    r_b_total = spy_actual_ytd
    spy_w = spy_full.dropna(subset=['spy_weight', 'tr_ytd'])
    r_b_capweighted = (spy_w['spy_weight'] * spy_w['tr_ytd']).sum() / spy_w['spy_weight'].sum()
    reconstitution_drift = r_b_capweighted - r_b_total

    is_off = df['sector'] == 'Off-Benchmark'
    df['port_r_f'] = df['port_r'].fillna(0)
    df['alloc'] = (df['port_w'] - df['bench_w']) * (df['bench_r'] - r_b_total)
    df['select'] = df['bench_w'] * (df['port_r_f'] - df['bench_r'])
    df['interact'] = (df['port_w'] - df['bench_w']) * (df['port_r_f'] - df['bench_r'])

    df.loc[is_off, 'alloc'] = df.loc[is_off, 'port_w'] * (df.loc[is_off, 'port_r_f'] - r_b_total)
    df.loc[is_off, 'select'] = 0.0
    df.loc[is_off, 'interact'] = 0.0

    df['total'] = df['alloc'] + df['select'] + df['interact']

    closed_only_mask = (df['port_w'] == 0) & (df['pnl'] != 0) & ~is_off
    closed_only_residual = (df.loc[closed_only_mask, 'pnl'] / cost_total).sum()

    return df.sort_values('total', ascending=False), {
        'alloc': df['alloc'].sum(),
        'select': df['select'].sum(),
        'interact': df['interact'].sum(),
        'closed_residual': closed_only_residual,
        'r_b_total': r_b_total,
        'r_b_capweighted': r_b_capweighted,
        'reconstitution_drift': reconstitution_drift,
    }


def compute_quant_edge(d):
    h5 = d['h5']
    spy = d['spy']
    held = h5[h5['市值'] > 0].copy()
    held_in_spy = held.merge(spy[['ticker', 'name_spy', 'sector', 'spy_weight', 'tr_ytd']], on='ticker', how='inner')

    spy_clean = spy.dropna(subset=['tr_ytd'])
    spy_clean = spy_clean.copy()
    spy_clean['rank_full'] = spy_clean['tr_ytd'].rank(pct=True) * 100
    held_in_spy = held_in_spy.merge(spy_clean[['ticker', 'rank_full']], on='ticker', how='left')

    profitable_held = (held['P&L(YTD)'] > 0).sum()
    profitable_all = (h5['P&L(YTD)'] > 0).sum()
    spy_actual = d['totals'].get('spy_ytd', 0)

    # Off-bench for narrative
    off_bench_df = held.merge(spy[['ticker', 'sector']], on='ticker', how='left')
    off_bench_df = off_bench_df[off_bench_df['sector'].isna()].copy()

    return {
        'held_in_spy': held_in_spy.sort_values('rank_full', ascending=False),
        'n_held': len(held),
        'n_all': len(h5),
        'profitable_held': int(profitable_held),
        'profitable_all': int(profitable_all),
        'hit_rate_held': profitable_held / len(held),
        'hit_rate_all': profitable_all / len(h5),
        'spy_avg_uw': spy_clean['tr_ytd'].mean(),
        'spy_avg_cw': spy_actual,  # 此鍵保留以避免下游 break, 語意改為「SPY 實際 YTD」
        'spy_actual': spy_actual,
        'port_avg_uw': held_in_spy['tr_ytd'].mean(),
        'mean_pct': held_in_spy['rank_full'].mean(),
        'pct_above_50': (held_in_spy['rank_full'] > 50).sum() / len(held_in_spy) * 100,
        'pct_top25': (held_in_spy['rank_full'] > 75).sum() / len(held_in_spy) * 100,
        'spy_clean': spy_clean,
        'off_bench': off_bench_df,
    }


# =============================================================
# Chart builders
# =============================================================
def chart_return_comparison(perf):
    labels = ['組合 (簡單法)', '組合 (Modified Dietz)', 'SPY 母體']
    values = [perf['simple_return'] * 100, perf['md_return'] * 100, perf['spy_return'] * 100]
    colors = [COLOR_PORT, COLOR_INFO, COLOR_BENCH]
    fig = go.Figure(go.Bar(
        x=labels, y=values, marker_color=colors,
        text=[f'{v:+.2f}%' for v in values], textposition='outside',
        cliponaxis=False,
        hovertemplate='%{x}: %{y:+.2f}%<extra></extra>',
    ))
    y_max = max(values) if max(values) > 0 else 0
    fig.update_layout(
        title=f'期間報酬率比較 ({PERIOD_START.date()} ~ {PERIOD_END.date()})',
        yaxis_title='報酬率',
        yaxis_ticksuffix='%',
        yaxis_range=[0, y_max * 1.15],
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=380,
        showlegend=False,
        margin=dict(t=60, b=40, l=60, r=20),
    )
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
    fig.update_layout(
        title='P&L 結構分解 (USD M)',
        yaxis_title='金額 (百萬美元)',
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=380,
        showlegend=False,
        margin=dict(t=60, b=40, l=60, r=20),
    )
    return fig


def chart_top_holdings(holdings):
    top = holdings['held_sorted'].head(15).copy()
    top = top.iloc[::-1]
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=top['ticker'], x=top['權重']*100,
        orientation='h', name='組合權重',
        marker_color=COLOR_PORT,
        text=[f'{v*100:.2f}%' for v in top['權重']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y} 組合權重: %{x:.2f}%<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=top['ticker'], x=top['指數權重']*100,
        orientation='h', name='SPY 權重',
        marker_color=COLOR_BENCH, opacity=0.7,
        cliponaxis=False,
        hovertemplate='%{y} SPY 權重: %{x:.2f}%<extra></extra>',
    ))
    max_w = top['權重'].max() * 100
    fig.update_layout(
        title='Top 15 持股權重 vs SPY',
        xaxis_title='權重',
        xaxis_ticksuffix='%',
        xaxis_range=[0, max_w * 1.18],
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=500,
        barmode='group',
        margin=dict(t=60, b=40, l=80, r=60),
    )
    return fig


def chart_contributors(top10, bot10):
    df = pd.concat([top10, bot10], ignore_index=True)
    df = df.sort_values('P&L(YTD)', ascending=True)
    colors = [COLOR_POS if v > 0 else COLOR_NEG for v in df['P&L(YTD)']]
    fig = go.Figure(go.Bar(
        y=df['ticker'] + ' ' + df['公司名稱'].str[:18],
        x=df['P&L(YTD)']/1e6,
        orientation='h',
        marker_color=colors,
        text=[f'${v/1e6:+,.1f}M' for v in df['P&L(YTD)']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y}<br>P&L: $%{x:+,.2f}M<extra></extra>',
    ))
    pnl_max = df['P&L(YTD)'].max() / 1e6
    pnl_min = df['P&L(YTD)'].min() / 1e6
    pad = max(abs(pnl_max), abs(pnl_min)) * 0.2
    fig.update_layout(
        title='Top 10 / Bottom 10 P&L 貢獻者',
        xaxis_title='P&L (USD M)',
        xaxis_range=[pnl_min - pad, pnl_max + pad],
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=620,
        showlegend=False,
        margin=dict(t=60, b=40, l=200, r=60),
    )
    return fig


def chart_sector_exposure(sector_df):
    df = sector_df.copy().sort_values('active_w_mv', ascending=True)
    fig = make_subplots(rows=1, cols=2, subplot_titles=['產業權重 vs SPY', '主動權重 (Active Weight)'],
                        horizontal_spacing=0.18)

    fig.add_trace(go.Bar(
        y=df['sector'], x=df['port_w_mv']*100, orientation='h',
        name='組合', marker_color=COLOR_PORT,
        text=[f'{v*100:.1f}%' for v in df['port_w_mv']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y} 組合: %{x:.2f}%<extra></extra>',
    ), row=1, col=1)
    fig.add_trace(go.Bar(
        y=df['sector'], x=df['bench_w']*100, orientation='h',
        name='SPY', marker_color=COLOR_BENCH, opacity=0.7,
        cliponaxis=False,
        hovertemplate='%{y} SPY: %{x:.2f}%<extra></extra>',
    ), row=1, col=1)

    colors = [COLOR_POS if v > 0 else COLOR_NEG for v in df['active_w_mv']]
    fig.add_trace(go.Bar(
        y=df['sector'], x=df['active_w_mv']*100, orientation='h',
        marker_color=colors, showlegend=False,
        text=[f'{v*100:+.1f}%' for v in df['active_w_mv']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y} Active: %{x:+.2f}%<extra></extra>',
    ), row=1, col=2)

    # 留 label 空間: 左圖 0~max+15, 右圖 active 範圍兩端各加 padding
    max_w = df[['port_w_mv','bench_w']].max().max() * 100
    aw_min = df['active_w_mv'].min() * 100
    aw_max = df['active_w_mv'].max() * 100
    aw_pad = max(abs(aw_min), abs(aw_max)) * 0.25
    fig.update_xaxes(ticksuffix='%', row=1, col=1, range=[0, max_w + 12])
    fig.update_xaxes(ticksuffix='%', row=1, col=2, range=[aw_min - aw_pad, aw_max + aw_pad])
    fig.update_layout(
        template=PLOTLY_TEMPLATE, font=CHART_FONT, height=520,
        barmode='group', margin=dict(t=60, b=40, l=180, r=60),
    )
    return fig


def chart_attribution_waterfall(attr_summary, actual_active):
    drift = attr_summary.get('reconstitution_drift', 0)
    items = [
        ('Allocation\n(產業tilt)', attr_summary['alloc']),
        ('Selection\n(個股選擇)', attr_summary['select']),
        ('Interaction', attr_summary['interact']),
        ('Closed-Only\n殘差', attr_summary['closed_residual']),
        ('Reconstitution\nDrift', drift),
        ('Active Return\n(vs SPY 實際)', actual_active),
    ]
    measures = ['relative', 'relative', 'relative', 'relative', 'relative', 'total']
    fig = go.Figure(go.Waterfall(
        name='Brinson',
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
        title='Brinson-Fachler 歸因瀑布圖 (相對 SPY 超額報酬)',
        yaxis_title='貢獻 (%)',
        yaxis_ticksuffix='%',
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=440,
        margin=dict(t=60, b=60, l=60, r=20),
    )
    return fig


def chart_sector_attribution(attr_df):
    df = attr_df.copy()
    df = df.sort_values('total', ascending=True)
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=df['sector'], x=df['alloc']*100, orientation='h', name='Allocation',
        marker_color=COLOR_INFO,
        hovertemplate='%{y} Alloc: %{x:+.2f}%<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=df['sector'], x=df['select']*100, orientation='h', name='Selection',
        marker_color=COLOR_ACCENT,
        hovertemplate='%{y} Sel: %{x:+.2f}%<extra></extra>',
    ))
    fig.add_trace(go.Bar(
        y=df['sector'], x=df['interact']*100, orientation='h', name='Interaction',
        marker_color=COLOR_POS,
        hovertemplate='%{y} Int: %{x:+.2f}%<extra></extra>',
    ))
    fig.update_layout(
        title='各 Sector Brinson 歸因效果分解 (相對 SPY)',
        xaxis_title='貢獻 (%)',
        xaxis_ticksuffix='%',
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=500,
        barmode='relative',
        margin=dict(t=60, b=40, l=180, r=40),
    )
    return fig


def chart_spy_percentile(quant):
    df = quant['held_in_spy'].copy()
    df = df.sort_values('rank_full', ascending=True)
    colors = [COLOR_POS if v > 50 else COLOR_NEG for v in df['rank_full']]
    fig = go.Figure(go.Bar(
        y=df['ticker'], x=df['rank_full'], orientation='h',
        marker_color=colors,
        text=[f'{v:.0f}' for v in df['rank_full']],
        textposition='outside', cliponaxis=False,
        hovertemplate='%{y} SPY 分位: %{x:.1f}<br>YTD: %{customdata[0]:+.1f}%<extra></extra>',
        customdata=df[['tr_ytd']].values * 100,
    ))
    fig.add_vline(x=50, line_dash='dash', line_color='#888',
                  annotation_text='隨機選股 (50)', annotation_position='top')
    fig.add_vline(x=75, line_dash='dot', line_color='#888',
                  annotation_text='前 1/4 (75)', annotation_position='top')
    fig.update_layout(
        title=f'在倉持股於 SPY 母體 YTD% 百分位分布 (平均 {quant["mean_pct"]:.1f}, 越高越好)',
        xaxis_title='SPY 百分位 (越高 = YTD 表現越好)',
        xaxis_range=[0, 105],
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=560,
        showlegend=False,
        margin=dict(t=80, b=40, l=80, r=60),
    )
    return fig


def chart_monthly_trades(d):
    tr = d['tr'].copy()
    tr['month'] = tr['交易日期'].dt.to_period('M').astype(str)
    buys = tr[tr['side']=='買'].groupby('month').agg(n=('amount','count'), amt=('amount','sum'))
    sells = tr[tr['side']=='賣'].groupby('month').agg(n=('amount','count'), amt=('amount','sum'))

    months = sorted(set(buys.index) | set(sells.index))
    buy_amt = [buys['amt'].get(m, 0)/1e6 for m in months]
    sell_amt = [sells['amt'].get(m, 0)/1e6 for m in months]
    buy_n = [buys['n'].get(m, 0) for m in months]
    sell_n = [sells['n'].get(m, 0) for m in months]

    fig = go.Figure()
    fig.add_trace(go.Bar(x=months, y=buy_amt, name='買進 ($M)', marker_color=COLOR_POS,
                       text=[f'{int(n)}筆' for n in buy_n], textposition='outside',
                       hovertemplate='%{x} 買進: $%{y:.1f}M<extra></extra>'))
    fig.add_trace(go.Bar(x=months, y=[-v for v in sell_amt], name='賣出 ($M)', marker_color=COLOR_NEG,
                       text=[f'{int(n)}筆' for n in sell_n], textposition='outside',
                       hovertemplate='%{x} 賣出: $%{y:.1f}M<extra></extra>'))
    fig.update_layout(
        title='月度交易節奏',
        xaxis_title='月份', yaxis_title='交易金額 (USD M)',
        xaxis=dict(type='category'),  # 強制當作類別, 避免 plotly 自動視為日期解析錯誤
        template=PLOTLY_TEMPLATE,
        font=CHART_FONT,
        height=400,
        barmode='relative',
        margin=dict(t=60, b=40, l=60, r=20),
    )
    return fig


# =============================================================
# HTML rendering
# =============================================================
def fig_to_div(fig, div_id):
    return pio.to_html(fig, full_html=False, include_plotlyjs=False, div_id=div_id, config={'displaylogo': False})


def df_to_html_table(df, columns=None, formatters=None, classes='data-table'):
    if columns:
        df = df[columns].copy()
    if formatters:
        for col, fn in formatters.items():
            if col in df.columns:
                df[col] = df[col].apply(fn)
    return df.to_html(classes=classes, index=False, escape=False, border=0)


def build_html(d, results=None):
    """
    若 results=None (standalone 執行): 重新 compute_*
    若 results 由 portfolio_analysis.main() 傳入: 直接使用同一次 console 分析的結果, 不重算
    """
    if results is None:
        print('  [HTML] 資料來源: recompute (standalone 模式)')
        perf = compute_performance(d)
        pnl_d = compute_pnl_decomp(d)
        holdings = compute_holdings(d)
        sector_df = compute_sector(d)
        attr_df, attr_summary = compute_attribution(sector_df, d['spy'], d['totals']['cost_end'], d['totals']['spy_ytd'])
        quant = compute_quant_edge(d)
        data_source = 'standalone-recompute'
    else:
        print('  [HTML] 資料來源: 沿用 console 分析結果 (no recompute)')
        perf = results['perf']
        pnl_d = results['holdings']['pnl_decomp']
        holdings = results['holdings']
        sector_df = results['sector_df']
        attr_df = results['attr_df']
        attr_summary = results['attr_summary']
        quant = results['quant']
        data_source = 'shared-from-console'

    actual_active = perf['simple_return'] - perf['spy_return']

    # ---- 動態敘述事實 (避免 narrative 寫死) ----
    months_in_period = DAYS / 30.0
    top1 = holdings['held_sorted'].iloc[0]
    top1_ticker = str(top1['ticker']).split()[0]

    # 最大超配 / 避開的 sector
    sec_df = sector_df.copy()
    overweight = sec_df[sec_df['active_w_mv'] > 0.005].sort_values('active_w_mv', ascending=False)
    avoided = sec_df[(sec_df['port_w_mv'] == 0) & (sec_df['bench_w'] > 0)].sort_values('bench_w', ascending=False)
    overweight_desc = ' / '.join(
        f"{r['sector']} {r['active_w_mv']*100:+.1f}%" for _, r in overweight.head(3).iterrows()
    ) if len(overweight) else 'n/a'
    avoided_names = '、'.join(avoided['sector'].tolist()) if len(avoided) else '無'
    avoided_w_sum = avoided['bench_w'].sum() if len(avoided) else 0

    # Off-bench summary
    off_b = quant.get('off_bench', pd.DataFrame())
    off_tickers = '、'.join([str(t).split()[0] for t in off_b['ticker'].tolist()]) if len(off_b) else 'n/a'
    off_n = len(off_b)
    off_w_total = off_b['權重'].sum() if len(off_b) else 0
    off_pnl_total = off_b['P&L(YTD)'].sum() if len(off_b) else 0
    off_pnl_share = off_pnl_total / pnl_d['total'] if pnl_d['total'] else 0

    # Brinson 最大貢獻 sector
    top_attr = attr_df.iloc[0]
    top_attr_sector = top_attr['sector']
    top_attr_total = top_attr['total']
    top_attr_port_r = top_attr['port_r'] if pd.notna(top_attr['port_r']) else 0
    top_attr_bench_r = top_attr['bench_r']
    top_attr_active_w = sec_df.loc[sec_df['sector'] == top_attr_sector, 'active_w_mv'].iloc[0] if (sec_df['sector'] == top_attr_sector).any() else 0

    # Quant edge top / bottom names
    held_in_spy = quant['held_in_spy']
    top_pct_names = '、'.join(held_in_spy.head(5)['ticker'].str.split().str[0].tolist())
    bot_pct = held_in_spy.tail(2).iloc[::-1]  # 2 worst, worst first
    bot_pct_desc = '、'.join(
        f"{str(r['ticker']).split()[0]} ({r['tr_ytd']*100:+.1f}%)"
        for _, r in bot_pct.iterrows()
    ) if len(bot_pct) else 'n/a'
    bot_worst = held_in_spy.iloc[-1] if len(held_in_spy) else None
    bot_worst_desc = (
        f"{str(bot_worst['ticker']).split()[0]} 落在 SPY 第 {bot_worst['rank_full']:.1f} 百分位 (後 {100 - bot_worst['rank_full']:.1f}%)"
        if bot_worst is not None else 'n/a'
    )

    # 月度交易 peak
    tr_df = d['tr'].copy()
    tr_df['month'] = tr_df['交易日期'].dt.to_period('M').astype(str)
    monthly_buy = tr_df[tr_df['side']=='買'].groupby('month').agg(n=('amount','count'), amt=('amount','sum'))
    monthly_sell = tr_df[tr_df['side']=='賣'].groupby('month').agg(n=('amount','count'), amt=('amount','sum'))
    if len(monthly_buy):
        peak_buy_month = monthly_buy['amt'].idxmax()
        peak_buy_amt = monthly_buy.loc[peak_buy_month, 'amt']
        peak_buy_n = int(monthly_buy.loc[peak_buy_month, 'n'])
    else:
        peak_buy_month, peak_buy_amt, peak_buy_n = 'n/a', 0, 0
    # 第一個淨賣超的月份
    net_by_month = monthly_buy['amt'].subtract(monthly_sell['amt'], fill_value=0)
    net_sell_months = net_by_month[net_by_month < 0]
    first_net_sell = net_sell_months.index[0] if len(net_sell_months) else 'n/a'

    # Return direction (MD vs Simple)
    md_vs_simple_text = '低估' if perf['md_return'] > perf['simple_return'] else '高估'
    rcg_text = '已實現淨損, 多為止損' if pnl_d['rcg'] < 0 else '已實現淨利'

    # Build charts
    figs = {
        'return': chart_return_comparison(perf),
        'pnl': chart_pnl_decomp(pnl_d),
        'top_holdings': chart_top_holdings(holdings),
        'contrib': chart_contributors(holdings['top10'], holdings['bot10']),
        'sector': chart_sector_exposure(sector_df),
        'waterfall': chart_attribution_waterfall(attr_summary, actual_active),
        'sector_attr': chart_sector_attribution(attr_df),
        'percentile': chart_spy_percentile(quant),
        'monthly': chart_monthly_trades(d),
    }

    # Tables
    # Top contributors
    top10_html = df_to_html_table(
        holdings['top10'],
        columns=['ticker', '公司名稱', 'URCG(YTD)', 'RCG(YTD)', 'DVD(YTD)', 'P&L(YTD)', '損益貢獻%'],
        formatters={
            'URCG(YTD)': lambda x: fmt_usd(x, 0),
            'RCG(YTD)': lambda x: fmt_usd(x, 0),
            'DVD(YTD)': lambda x: fmt_usd(x, 0),
            'P&L(YTD)': lambda x: fmt_usd(x, 0),
            '損益貢獻%': lambda x: fmt_pct(x, 2),
        }
    )
    bot10_html = df_to_html_table(
        holdings['bot10'],
        columns=['ticker', '公司名稱', 'URCG(YTD)', 'RCG(YTD)', 'DVD(YTD)', 'P&L(YTD)', '損益貢獻%'],
        formatters={
            'URCG(YTD)': lambda x: fmt_usd(x, 0),
            'RCG(YTD)': lambda x: fmt_usd(x, 0),
            'DVD(YTD)': lambda x: fmt_usd(x, 0),
            'P&L(YTD)': lambda x: fmt_usd(x, 0),
            '損益貢獻%': lambda x: fmt_pct(x, 2),
        }
    )

    # Sector attribution table
    attr_table_df = attr_df.copy()
    attr_table_df = attr_table_df[['sector', 'port_w', 'bench_w', 'port_r', 'bench_r', 'alloc', 'select', 'interact', 'total']]
    attr_table_df.columns = ['Sector', 'wP(cost)', 'wB(SPY)', 'rP', 'rB', 'Alloc', 'Select', 'Interact', 'Total']
    attr_html = df_to_html_table(
        attr_table_df,
        formatters={
            'wP(cost)': lambda x: fmt_pct(x, 2, sign=False),
            'wB(SPY)': lambda x: fmt_pct(x, 2, sign=False),
            'rP': lambda x: fmt_pct(x, 2) if pd.notna(x) and x != 0 else 'n/a',
            'rB': lambda x: fmt_pct(x, 2) if pd.notna(x) and x != 0 else 'n/a',
            'Alloc': lambda x: fmt_pct(x, 3),
            'Select': lambda x: fmt_pct(x, 3),
            'Interact': lambda x: fmt_pct(x, 3),
            'Total': lambda x: fmt_pct(x, 3),
        }
    )

    # Holdings vs SPY table
    held_in_spy_disp = quant['held_in_spy'].copy()
    held_in_spy_disp = held_in_spy_disp[['ticker', '公司名稱', 'sector', '權重', 'P&L(YTD)', 'tr_ytd', 'rank_full']]
    held_in_spy_disp.columns = ['Ticker', '公司', 'Sector', '組合權重', 'P&L', 'YTD%', 'SPY百分位']
    held_html = df_to_html_table(
        held_in_spy_disp,
        formatters={
            '組合權重': lambda x: fmt_pct(x, 2, sign=False),
            'P&L': lambda x: fmt_usd(x, 0),
            'YTD%': lambda x: fmt_pct(x, 2),
            'SPY百分位': lambda x: f'{x:.1f}',
        }
    )

    # Off-bench table
    h5 = d['h5']
    held = h5[h5['市值'] > 0].merge(d['spy'][['ticker', 'sector']], on='ticker', how='left')
    off_bench = held[held['sector'].isna()].copy()
    off_bench['return'] = off_bench['P&L(YTD)'] / off_bench['庫存成本(YTD)']
    off_bench_html = df_to_html_table(
        off_bench[['ticker', '公司名稱', '市值', '權重', 'P&L(YTD)', 'return']],
        formatters={
            '市值': lambda x: fmt_usd(x, 0),
            '權重': lambda x: fmt_pct(x, 2, sign=False),
            'P&L(YTD)': lambda x: fmt_usd(x, 0),
            'return': lambda x: fmt_pct(x, 2),
        }
    )

    # Build HTML
    div_return = fig_to_div(figs['return'], 'fig_return')
    div_pnl = fig_to_div(figs['pnl'], 'fig_pnl')
    div_top = fig_to_div(figs['top_holdings'], 'fig_top')
    div_contrib = fig_to_div(figs['contrib'], 'fig_contrib')
    div_sector = fig_to_div(figs['sector'], 'fig_sector')
    div_waterfall = fig_to_div(figs['waterfall'], 'fig_waterfall')
    div_sector_attr = fig_to_div(figs['sector_attr'], 'fig_sector_attr')
    div_percentile = fig_to_div(figs['percentile'], 'fig_percentile')
    div_monthly = fig_to_div(figs['monthly'], 'fig_monthly')

    # plotly.js loaded from CDN (約 3.5MB → 用 CDN 避免內嵌過大檔案)
    plotly_cdn = '<script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>'

    html = f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>計量持股組合分析報告</title>
{plotly_cdn}
<style>
* {{ box-sizing: border-box; }}
body {{
    font-family: 'Microsoft JhengHei', 'Segoe UI', Arial, sans-serif;
    margin: 0; padding: 0;
    background: #F5F6FA;
    color: #2C3E50;
    line-height: 1.6;
}}
.container {{ max-width: 1400px; margin: 0 auto; padding: 24px; }}
header {{
    background: linear-gradient(135deg, #1F4E79 0%, #2C5F8F 100%);
    color: white;
    padding: 32px 24px;
    border-radius: 8px;
    margin-bottom: 24px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
}}
header h1 {{ margin: 0 0 8px 0; font-size: 28px; }}
header .meta {{ font-size: 14px; opacity: 0.9; }}
.kpi-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 14px;
    margin: 20px 0;
}}
.kpi-card {{
    background: white;
    padding: 18px;
    border-radius: 6px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
    border-left: 4px solid {COLOR_PORT};
}}
.kpi-card.pos {{ border-left-color: {COLOR_POS}; }}
.kpi-card.neg {{ border-left-color: {COLOR_NEG}; }}
.kpi-card.accent {{ border-left-color: {COLOR_ACCENT}; }}
.kpi-label {{ font-size: 12px; color: #7F8C8D; text-transform: uppercase; letter-spacing: 0.5px; }}
.kpi-value {{ font-size: 26px; font-weight: 600; margin-top: 4px; }}
.kpi-sub {{ font-size: 12px; color: #95A5A6; margin-top: 4px; }}
section {{
    background: white;
    margin: 20px 0;
    padding: 24px;
    border-radius: 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}}
section h2 {{
    margin: 0 0 16px 0;
    padding-bottom: 12px;
    border-bottom: 2px solid #ECF0F1;
    color: #1F4E79;
    font-size: 22px;
}}
section h3 {{
    margin: 20px 0 12px 0;
    color: #34495E;
    font-size: 16px;
}}
.narrative {{
    background: #F8F9FA;
    padding: 14px 18px;
    border-left: 4px solid {COLOR_INFO};
    margin: 14px 0;
    border-radius: 4px;
    font-size: 14px;
}}
.narrative strong {{ color: #1F4E79; }}
.highlight-pos {{ color: {COLOR_POS}; font-weight: 600; }}
.highlight-neg {{ color: {COLOR_NEG}; font-weight: 600; }}
.two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
@media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
table.data-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
    margin: 12px 0;
}}
table.data-table th {{
    background: #1F4E79; color: white; padding: 10px 8px; text-align: left;
    font-weight: 500;
}}
table.data-table td {{ padding: 8px; border-bottom: 1px solid #ECF0F1; }}
table.data-table tr:hover {{ background: #F8F9FA; }}
table.data-table td:nth-child(n+3) {{ text-align: right; font-variant-numeric: tabular-nums; }}
.method-note {{
    font-size: 12px; color: #7F8C8D; font-style: italic;
    margin-top: 8px; padding: 8px; background: #FAFBFC; border-radius: 4px;
}}
footer {{
    text-align: center; padding: 24px; color: #95A5A6; font-size: 12px;
}}
</style>
</head>
<body>
<div class="container">

<header>
    <h1>計量持股組合分析報告</h1>
    <div class="meta">
        分析期間: {PERIOD_START.date()} ~ {PERIOD_END.date()} ({DAYS} 天) ·
        基準: SPY (S&P 500) ·
        產出時間: {datetime.now():%Y-%m-%d %H:%M}
    </div>
</header>

<div class="kpi-grid">
    <div class="kpi-card pos">
        <div class="kpi-label">組合報酬 (TWRR)</div>
        <div class="kpi-value highlight-pos">{fmt_pct(perf['md_return'])}</div>
        <div class="kpi-sub">Modified Dietz</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">SPY 母體</div>
        <div class="kpi-value">{fmt_pct(perf['spy_return'])}</div>
        <div class="kpi-sub">YTD cap-weighted</div>
    </div>
    <div class="kpi-card pos">
        <div class="kpi-label">Active Return</div>
        <div class="kpi-value highlight-pos">{fmt_pct(perf['active_md'])}</div>
        <div class="kpi-sub">超額報酬 (vs SPY)</div>
    </div>
    <div class="kpi-card accent">
        <div class="kpi-label">Active Share</div>
        <div class="kpi-value">{fmt_pct(perf['active_share'], 1, sign=False)}</div>
        <div class="kpi-sub">vs SPY 結構差異</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期末 MV</div>
        <div class="kpi-value">${perf['mv_end']/1e6:,.0f}M</div>
        <div class="kpi-sub">{PERIOD_END.date()}</div>
    </div>
    <div class="kpi-card">
        <div class="kpi-label">期內淨買進</div>
        <div class="kpi-value">${perf['net_cf']/1e6:,.0f}M</div>
        <div class="kpi-sub">買 - 賣 (USD)</div>
    </div>
</div>

<section>
    <h2>1. 績效摘要</h2>
    <div class="narrative">
        組合期間 ({months_in_period:.1f} 個月) 達 <strong class="highlight-pos">{fmt_pct(perf['md_return'])}</strong> (Modified Dietz)，
        相對 SPY 的 {fmt_pct(perf['spy_return'])}，
        Active Return <strong class="highlight-pos">{fmt_pct(perf['active_md'])}</strong>。
        Active Share {fmt_pct(perf['active_share'], 1, sign=False)} 顯示組合結構與 SPY 明顯不同；
        期間 cost 從 {fmt_usd(d['totals']['cost_start']/1e6, 1)}M 變為 {fmt_usd(d['totals']['cost_end']/1e6, 0)}M
        (淨買進 {fmt_usd(perf['net_cf']/1e6, 0)}M)，簡單法因資金時間結構而{md_vs_simple_text}時間加權績效。
    </div>
    <div class="two-col">
        <div>{div_return}</div>
        <div>{div_pnl}</div>
    </div>
    <div class="narrative">
        P&L 結構：<strong class="highlight-pos">未實現損益 {fmt_usd(pnl_d['urcg']/1e6, 0)}M</strong>
        佔總 P&L {fmt_pct(pnl_d['urcg']/pnl_d['total'], 1)}，
        已實現損益 {fmt_usd(pnl_d['rcg']/1e6, 0)}M ({rcg_text})，
        股息 {fmt_usd(pnl_d['dvd']/1e6, 1)}M。
    </div>
    <p class="method-note">
        Modified Dietz: R = (V_end − V_start − Σ CF) / (V_start + Σ w_i × CF_i)，
        把每筆交易視為對 stock-portfolio 的外部 cash flow，使 TWRR 可直接與 SPY YTD% 比較。
    </p>
</section>

<section>
    <h2>2. 持股 (Top 15) 與 SPY 權重對照</h2>
    {div_top}
    <div class="narrative">
        最大持股 <strong>{top1_ticker}</strong> 權重 {fmt_pct(top1['權重'], 2, sign=False)}，
        對應 SPY 的 {fmt_pct(top1['指數權重'] if pd.notna(top1['指數權重']) else 0, 2, sign=False)}；
        前 10 大持股集中度 <strong>{fmt_pct(holdings['top10_w'], 1, sign=False)}</strong>，
        有效持股數 {holdings['eff_n']:.1f} 檔 (1/Σw²)。
    </div>
</section>

<section>
    <h2>3. P&L 貢獻者 (Top / Bottom 10)</h2>
    {div_contrib}
    <div class="two-col">
        <div>
            <h3>Top 10 貢獻 (含 URCG + RCG + DVD)</h3>
            {top10_html}
        </div>
        <div>
            <h3>Bottom 10 貢獻</h3>
            {bot10_html}
        </div>
    </div>
    <div class="narrative">
        Top 5 ({' / '.join(holdings['top10'].head(5)['ticker'].str.split().str[0])}) 共貢獻
        <strong class="highlight-pos">{fmt_usd(holdings['top10'].head(5)['P&L(YTD)'].sum()/1e6, 0)}M</strong>
        (約佔總 P&L {fmt_pct(holdings['top10'].head(5)['P&L(YTD)'].sum()/pnl_d['total'], 1, sign=False)})。
        Bottom 5 ({' / '.join(holdings['bot10'].head(5)['ticker'].str.split().str[0])}) 共拖累
        <strong class="highlight-neg">{fmt_usd(holdings['bot10'].head(5)['P&L(YTD)'].sum()/1e6, 1)}M</strong>。
    </div>
</section>

<section>
    <h2>4. 部位變化與週轉</h2>
    <div class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-label">期初持倉</div>
            <div class="kpi-value">{holdings['n_held_12']}</div>
            <div class="kpi-sub">{PERIOD_START.date()}</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">期末持倉</div>
            <div class="kpi-value">{holdings['n_held_5']}</div>
            <div class="kpi-sub">{PERIOD_END.date()}</div>
        </div>
        <div class="kpi-card accent">
            <div class="kpi-label">留任</div>
            <div class="kpi-value">{holdings['n_continued']}</div>
            <div class="kpi-sub">continued</div>
        </div>
        <div class="kpi-card pos">
            <div class="kpi-label">新進</div>
            <div class="kpi-value">{holdings['n_new']}</div>
            <div class="kpi-sub">new positions</div>
        </div>
        <div class="kpi-card neg">
            <div class="kpi-label">賣光</div>
            <div class="kpi-value">{holdings['n_sold_out']}</div>
            <div class="kpi-sub">sold out</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-label">有效持股數</div>
            <div class="kpi-value">{holdings['eff_n']:.1f}</div>
            <div class="kpi-sub">1/Σw²</div>
        </div>
    </div>
    {div_monthly}
    <div class="narrative">
        買進高峰: <strong>{peak_buy_month}</strong> ({peak_buy_n} 筆 / ${peak_buy_amt/1e6:,.0f}M)；
        首次淨賣超月份: <strong>{first_net_sell}</strong>。
    </div>
</section>

<section>
    <h2>5. 產業 (GICS) 曝險 vs SPY</h2>
    {div_sector}
    <div class="narrative">
        <strong>顯著超配 (Active Weight)</strong>: {overweight_desc}。<br>
        <strong>完全避開 (port_w=0 但 SPY 有曝險)</strong>: {avoided_names}
        (共佔 SPY {fmt_pct(avoided_w_sum, 1, sign=False)})。<br>
        <strong>Off-Benchmark</strong>: {off_tickers if off_n else '無'} 共 {off_n} 檔
        (~{fmt_pct(off_w_total, 1, sign=False)} 權重)，
        期內貢獻 {fmt_usd(off_pnl_total/1e6, 1)}M P&L ({fmt_pct(off_pnl_share, 1)} of total)。
    </div>
</section>

<section>
    <h2>6. Brinson-Fachler 歸因</h2>
    {div_waterfall}
    {div_sector_attr}
    <h3>各 Sector 細項</h3>
    {attr_html}
    <div class="narrative">
        <strong>歸因加總對帳</strong>:
        Allocation {fmt_pct(attr_summary['alloc'], 2)} +
        Selection {fmt_pct(attr_summary['select'], 2)} +
        Interaction {fmt_pct(attr_summary['interact'], 2)} +
        Closed-Only Residual {fmt_pct(attr_summary['closed_residual'], 2)} +
        Reconstitution Drift {fmt_pct(attr_summary.get('reconstitution_drift', 0), 2)}
        = {fmt_pct(attr_summary['alloc']+attr_summary['select']+attr_summary['interact']+attr_summary['closed_residual']+attr_summary.get('reconstitution_drift', 0), 2)}
        ≈ Active Return ({fmt_pct(actual_active, 2)})。<br>
        最大貢獻 sector <strong>{top_attr_sector}</strong>: total active
        <strong class="highlight-pos">{fmt_pct(top_attr_total, 2)}</strong>
        (active weight {fmt_pct(top_attr_active_w, 1)}, rP {fmt_pct(top_attr_port_r, 1)} vs rB {fmt_pct(top_attr_bench_r, 1) if top_attr_bench_r else 'n/a'})。
        Interaction Effect {fmt_pct(attr_summary['interact'], 2)} 反映超配 sector 與選股報酬的乘積效果。
    </div>
    <p class="method-note">
        Brinson-Fachler 標準公式: Alloc = (wP − wB)(rB_sec − rB_total)，
        Sel = wB × (rP_sec − rB_sec)，Int = (wP − wB)(rP_sec − rB_sec)。
        權重採成本基礎，報酬採 P&L / cost_end_basis (與報表口徑一致)。
        Off-Benchmark 用 wP × (rP − rB_total)；Closed-Only sectors 的 P&L 進入殘差行。<br>
        <strong>Reconstitution Drift</strong>: rB_total 使用 SPY 實際 YTD ({fmt_pct(attr_summary.get('r_b_total', 0), 2)})，
        但各 sector rB_sec 仍以當期成分股 cap-weighted ({fmt_pct(attr_summary.get('r_b_capweighted', 0), 2)})，
        差額為期間 SPY 成分股增減 / 替換造成的 drift。
    </p>
</section>

<section>
    <h2>7. 量化模型 Edge: 在倉持股 vs SPY 母體</h2>
    <div class="kpi-grid">
        <div class="kpi-card pos">
            <div class="kpi-label">在倉命中率</div>
            <div class="kpi-value">{quant['hit_rate_held']*100:.1f}%</div>
            <div class="kpi-sub">{int(quant['n_held'])} 檔 / {quant['profitable_held']} 獲利</div>
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
        <div class="kpi-card">
            <div class="kpi-label">持股等權平均 YTD</div>
            <div class="kpi-value highlight-pos">{fmt_pct(quant['port_avg_uw'])}</div>
            <div class="kpi-sub">vs SPY 等權 {fmt_pct(quant['spy_avg_uw'])}</div>
        </div>
    </div>
    {div_percentile}
    <div class="narrative">
        在倉的 SPY 成分股平均落在母體 <strong>{quant['mean_pct']:.1f}</strong> 百分位
        (50 = 隨機選股)，前 1/4 (百分位 &gt; 75) 占 <strong>{quant['pct_top25']:.1f}%</strong>。
        Top 5 高分位持股: <strong>{top_pct_names}</strong>。
        Bottom 拖累: {bot_pct_desc}；其中 {bot_worst_desc}。
    </div>
    <h3>在倉部位完整明細 (僅 SPY 成分)</h3>
    {held_html}
    <h3>Off-Benchmark 持股 (不在 SPY 內)</h3>
    {off_bench_html}
</section>

<footer>
    生成: portfolio_analysis.py + generate_html_report.py · Plotly {__import__('plotly').__version__} · 資料來源: {data_source}
</footer>

</div>
</body>
</html>
"""
    return html


def main():
    print('Loading data...')
    d = load_data(FP)
    print('Building HTML report...')
    html = build_html(d)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'✓ Saved: {OUTPUT_HTML}')
    print(f'  Size: {OUTPUT_HTML.stat().st_size / 1024:.1f} KB')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
