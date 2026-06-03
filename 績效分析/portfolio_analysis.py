# -*- coding: utf-8 -*-
"""
量化持股組合分析 (2025-12-31 → 2026-05-21)

資料來源: 計量持股分析.xlsx
基準: SPY (S&P 500)

分析項目:
  1. 績效摘要 (Modified Dietz + Simple)
  2. 持股貢獻 (Top/Bottom contributors, P&L decomposition)
  3. 產業 (GICS) 曝險 vs SPY (含 Off-Benchmark 桶)
  4. Brinson-Fachler 歸因 (Allocation / Selection / Interaction)
  5. 交易分析 (週轉率、勝率、平均持有期)
  6. 量化模型 Edge (命中率、相對 SPY 個股勝率、產業 tilt)
"""
import sys, io
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

pd.set_option('display.float_format', lambda x: f'{x:,.4f}' if abs(x) < 1 else f'{x:,.2f}')
pd.set_option('display.width', 200)
pd.set_option('display.max_columns', None)

FP = Path(__file__).parent / '計量持股分析.xlsx'
PERIOD_START = pd.Timestamp('2025-12-31')
PERIOD_END = pd.Timestamp('2026-05-21')
DAYS = (PERIOD_END - PERIOD_START).days  # 142


# =============================================================
# Helpers
# =============================================================
def hr(title, char='='):
    line = char * 78
    print(f'\n{line}\n  {title}\n{line}')

def sub(title):
    print(f'\n— {title} —')

def pct(x, digits=2):
    if pd.isna(x): return '   n/a'
    return f'{x*100:+.{digits}f}%'

def usd(x, digits=0):
    if pd.isna(x): return '   n/a'
    return f'${x:,.{digits}f}'

def norm_ticker(t):
    if pd.isna(t): return None
    t = str(t).strip()
    if t.endswith(' Equity'):
        t = t[:-7]
    return t


# =============================================================
# 1. Data loader
# =============================================================
def load_data(fp):
    # 5/21 holdings (header on row index 2)
    h5 = pd.read_excel(fp, sheet_name='持股分析(20260521)', header=2)
    h5 = h5.dropna(subset=['代碼']).reset_index(drop=True)
    h5 = h5[h5['代碼'] != 'BBG_TICKER'].reset_index(drop=True)  # 移除 BQL 表頭殘留
    num_cols_5 = ['庫存成本(YTD)', '市值', 'URCG(YTD)', 'URCG%',
                  'RCG(YTD)', 'DVD(YTD)', 'P&L(YTD)', '損益貢獻%',
                  '權重', '指數權重', '權重差異']
    for c in num_cols_5:
        h5[c] = pd.to_numeric(h5[c], errors='coerce')
    h5['ticker'] = h5['代碼'].apply(norm_ticker)

    # 12/31 holdings (header on row index 2)
    h12 = pd.read_excel(fp, sheet_name='持股庫存(20251231)', header=2)
    h12 = h12.dropna(subset=['代碼']).reset_index(drop=True)
    h12 = h12[h12['代碼'] != 'BBG_TICKER'].reset_index(drop=True)
    num_cols_12 = ['庫存成本(YTD)', '市值', 'URCG(YTD)', 'RCG(YTD)',
                   'DVD(YTD)', 'P&L(YTD)']
    for c in num_cols_12:
        h12[c] = pd.to_numeric(h12[c], errors='coerce')
    h12['ticker'] = h12['代碼'].apply(norm_ticker)

    # Trades
    tr = pd.read_excel(fp, sheet_name='交易分析')
    tr['交易日期'] = pd.to_datetime(tr['交易日期'])
    tr['ticker'] = tr['股票代碼(代碼)'].apply(norm_ticker)
    tr = tr.rename(columns={
        '交易型態': 'side', '股數': 'qty',
        '單價(原幣)': 'price', '交易淨額(原幣)': 'amount',
        '平均成本(原幣)': 'avg_cost', '交易成本(原幣)': 'cost',
        '價差損益(原幣)': 'realized_pnl'
    })
    tr['signed_qty'] = np.where(tr['side'] == '買', tr['qty'], -tr['qty'])
    tr['signed_amount'] = np.where(tr['side'] == '買', tr['amount'], -tr['amount'])

    # SPY 實際 YTD Return (row 0, col 2 — 由 Bloomberg/SPY 直接取得, 避免用成分股市值權重自算)
    raw_spy = pd.read_excel(fp, sheet_name='S&P500(20260521)', header=None)
    spy_actual_ytd = float(raw_spy.iloc[0, 2])

    # SPY constituents (header 仍在 row 1)
    spy = pd.read_excel(fp, sheet_name='S&P500(20260521)', header=1)
    spy = spy.dropna(subset=['ID']).reset_index(drop=True)
    spy['ticker'] = spy['ID'].apply(norm_ticker)
    spy = spy.rename(columns={'name': 'name_spy', 'weights': 'spy_weight',
                              'GICS_Sector': 'sector', 'YTD%': 'tr_ytd'})
    spy = spy[['ticker', 'name_spy', 'spy_weight', 'sector', 'tr_ytd']]
    spy['spy_weight'] = pd.to_numeric(spy['spy_weight'], errors='coerce')
    spy['tr_ytd'] = pd.to_numeric(spy['tr_ytd'], errors='coerce')

    # Top-level totals
    raw5 = pd.read_excel(fp, sheet_name='持股分析(20260521)')
    raw12 = pd.read_excel(fp, sheet_name='持股庫存(20251231)')
    totals = {
        'cost_end': float(raw5.iloc[0, 2]),
        'mv_end':   float(raw5.iloc[0, 3]),
        'pnl_end':  float(raw5.iloc[0, 8]),
        'return_simple': float(raw5.iloc[0, 9]),
        'active_share': float(raw5.iloc[0, 12]),
        'index_overlap_weight': float(raw5.iloc[0, 11]),
        'cost_start': float(raw12.iloc[0, 2]),
        'mv_start':   float(raw12.iloc[0, 3]),
        'pnl_2025':   float(raw12.iloc[0, 7]),
        'spy_ytd':    spy_actual_ytd,  # 來自 Bloomberg SPY US Equity 直接報價
    }

    return {'h5': h5, 'h12': h12, 'tr': tr, 'spy': spy, 'totals': totals}


# =============================================================
# 2. Performance summary
# =============================================================
def section_performance(d):
    hr('1. 績效摘要 (2025-12-31 → 2026-05-21, 142 天)')
    t = d['totals']
    tr = d['tr']
    spy = d['spy']

    # ----- Modified Dietz -----
    # 將 "stock portfolio" 視為一個帳戶: 買進=資金流入, 賣出=資金流出
    # V_end - V_start - Σ CF = 純報酬 (扣除外部現金流)
    # 分母 = V_start + Σ w_i × CF_i ; w_i = (T - t_i)/T
    cfs = tr.copy()
    cfs['days_since_start'] = (cfs['交易日期'] - PERIOD_START).dt.days
    cfs['weight'] = (DAYS - cfs['days_since_start']) / DAYS
    cfs['weighted_cf'] = cfs['signed_amount'] * cfs['weight']

    net_cf = cfs['signed_amount'].sum()
    weighted_cf = cfs['weighted_cf'].sum()
    denom = t['mv_start'] + weighted_cf
    md_return = (t['mv_end'] - t['mv_start'] - net_cf) / denom

    # ----- Simple return on end-cost (報表口徑) -----
    simple_return = t['pnl_end'] / t['cost_end']

    # ----- Net invested capital change -----
    delta_mv = t['mv_end'] - t['mv_start']
    delta_cost = t['cost_end'] - t['cost_start']

    # ----- SPY return (取自 Bloomberg SPY US Equity 實際 YTD, 不用成分股自算) -----
    # 期間 SPY 有成分股調整, cap-weighted 自算與實際 SPY 績效會有 drift
    spy_tr = t['spy_ytd']

    print(f"期初 MV (2025-12-31)          : {usd(t['mv_start'])}")
    print(f"期末 MV (2026-05-21)          : {usd(t['mv_end'])}")
    print(f"期末庫存成本                  : {usd(t['cost_end'])}")
    print(f"期末總損益                    : {usd(t['pnl_end'])}")
    print(f"期間淨買進 (買 - 賣)          : {usd(net_cf)}")
    print()
    print(f"報酬率口徑:")
    print(f"  [A] 簡單法 (P&L/期末cost)    : {pct(simple_return)}    ← 報表口徑")
    print(f"  [B] Modified Dietz (時間加權): {pct(md_return)}    ← 績效比較口徑")
    print(f"  [C] SPY YTD (cap-weighted)   : {pct(spy_tr)}")
    print()
    print(f"超額報酬 (Active Return):")
    print(f"  [A] vs SPY : {pct(simple_return - spy_tr)}")
    print(f"  [B] vs SPY : {pct(md_return - spy_tr)}")
    print()
    print(f"Active Share                  : {pct(t['active_share'])}")
    print(f"SPY 權重涵蓋率 (overlap)      : {pct(t['index_overlap_weight'])}")
    print()
    print("  [說明] Modified Dietz: 將每筆交易視為對 stock-portfolio 帳戶的外部 CF；")
    print("  其結果可直接與 SPY YTD% 比較。簡單法不考慮資金時間價值，會因為")
    print("  期間大量加碼 (cost 141M → 668M) 而被低估。")

    return {
        'simple_return': simple_return,
        'md_return': md_return,
        'spy_return': spy_tr,
        'active_simple': simple_return - spy_tr,
        'active_md': md_return - spy_tr,
        'net_cf': net_cf,
        'mv_start': t['mv_start'],
        'mv_end': t['mv_end'],
        'cost_start': t['cost_start'],
        'cost_end': t['cost_end'],
        'pnl_end': t['pnl_end'],
        'active_share': t['active_share'],
        'overlap': t['index_overlap_weight'],
    }


# =============================================================
# 3. Holdings analysis
# =============================================================
def section_holdings(d):
    hr('2. 持股與貢獻分析')
    h5 = d['h5'].copy()
    h12 = d['h12'].copy()

    held = h5[h5['市值'] > 0].copy()
    closed = h5[h5['市值'] == 0].copy()
    t = d['totals']

    # ----- Top / Bottom contributors -----
    sub('Top 10 P&L 貢獻者 (含未實現+已實現+股息)')
    top10 = h5.nlargest(10, 'P&L(YTD)')[
        ['ticker', '公司名稱', 'URCG(YTD)', 'RCG(YTD)', 'DVD(YTD)',
         'P&L(YTD)', '損益貢獻%']
    ]
    print(top10.to_string(index=False))

    sub('Bottom 10 P&L 貢獻者')
    bot10 = h5.nsmallest(10, 'P&L(YTD)')[
        ['ticker', '公司名稱', 'URCG(YTD)', 'RCG(YTD)', 'DVD(YTD)',
         'P&L(YTD)', '損益貢獻%']
    ]
    print(bot10.to_string(index=False))

    # ----- P&L decomposition -----
    sub('P&L 結構分解')
    total_urcg = h5['URCG(YTD)'].sum()
    total_rcg = h5['RCG(YTD)'].sum()
    total_dvd = h5['DVD(YTD)'].sum()
    total_pnl = h5['P&L(YTD)'].sum()
    print(f"  未實現損益 (URCG)   : {usd(total_urcg):>20}   {pct(total_urcg/total_pnl)}")
    print(f"  已實現損益 (RCG)    : {usd(total_rcg):>20}   {pct(total_rcg/total_pnl)}")
    print(f"  股息收入   (DVD)    : {usd(total_dvd):>20}   {pct(total_dvd/total_pnl)}")
    print(f"  ----------------------------------------------------------------")
    print(f"  總損益             : {usd(total_pnl):>20}   100.00%")

    # ----- Position turnover -----
    sub('部位變化')
    set_5 = set(held['ticker'])
    set_12 = set(h12[h12['市值'] > 0]['ticker'])
    continued = set_12 & set_5
    new_pos = set_5 - set_12
    sold_out = set_12 - set_5

    print(f"  12/31 持倉檔數      : {len(set_12)}")
    print(f"  5/21  持倉檔數      : {len(set_5)}")
    print(f"  └ 留任 (continued)  : {len(continued)}")
    print(f"  └ 新進 (new)        : {len(new_pos)}")
    print(f"  └ 賣光 (sold out)   : {len(sold_out)}")
    print(f"  期內曾出現過 (含已平倉): {len(set_5 | set_12 | set(d['tr']['ticker']))}")

    # ----- Concentration -----
    sub('集中度')
    held_sorted = held.sort_values('權重', ascending=False)
    top5 = held_sorted.head(5)['權重'].sum()
    top10w = held_sorted.head(10)['權重'].sum()
    hhi = (held['權重'] ** 2).sum() * 10000  # Herfindahl-Hirschman (0-10000)
    eff_n = 1 / (held['權重'] ** 2).sum()  # Effective number of holdings
    print(f"  Top  5 權重占比     : {pct(top5)}")
    print(f"  Top 10 權重占比     : {pct(top10w)}")
    print(f"  Herfindahl Index    : {hhi:,.1f}  (越高越集中)")
    print(f"  有效持股數          : {eff_n:.1f}  (vs SPY ≈ 60-80, 越低越集中)")

    return {
        'held': held,
        'held_sorted': held_sorted,
        'top10': top10,
        'bot10': bot10,
        'pnl_decomp': {
            'urcg': total_urcg, 'rcg': total_rcg,
            'dvd': total_dvd, 'total': total_pnl,
        },
        'n_continued': len(continued),
        'n_new': len(new_pos),
        'n_sold_out': len(sold_out),
        'n_held_5': len(set_5),
        'n_held_12': len(set_12),
        'top5_w': top5,
        'top10_w': top10w,
        'hhi': hhi,
        'eff_n': eff_n,
    }


# =============================================================
# 4. Sector exposure
# =============================================================
def section_sector(d):
    hr('3. 產業 (GICS) 曝險 vs SPY')
    h5 = d['h5']
    spy = d['spy']

    # 把所有期內出現的部位都用來 map sector (含已平倉),
    # 這樣已平倉部位的 P&L 也會進入對應產業, 歸因才能 reconcile
    all_pos = h5.copy()
    all_pos = all_pos.merge(spy[['ticker', 'sector']], on='ticker', how='left')
    all_pos['sector'] = all_pos['sector'].fillna('Off-Benchmark')

    held = all_pos[all_pos['市值'] > 0].copy()
    closed = all_pos[all_pos['市值'] == 0].copy()

    # Portfolio sector aggregation: 權重用 held 的 cost / 市值, P&L 含已平倉
    port_mv_total = held['市值'].sum()
    port_cost_total = held['庫存成本(YTD)'].sum()
    port_sector = all_pos.groupby('sector').agg(
        mv=('市值', 'sum'),
        cost=('庫存成本(YTD)', 'sum'),
        pnl=('P&L(YTD)', 'sum'),
        n=('ticker', 'count')
    ).reset_index()
    # MV-based weight (報表口徑 — 顯示用)
    port_sector['port_w_mv'] = port_sector['mv'] / port_mv_total
    # Cost-based weight (歸因用 — 與 cost-based return 配對, 確保總和 reconcile)
    port_sector['port_w'] = port_sector['cost'] / port_cost_total
    # 報酬 = (含已平倉的所有 P&L in sector) / (held 的 cost in sector)
    # Σ port_w × port_r = Σ P&L / total_cost = 簡單法總報酬 (含已平倉 P&L)
    port_sector['port_r'] = port_sector['pnl'] / port_sector['cost'].replace(0, np.nan)

    # SPY sector weights
    spy_clean = spy.dropna(subset=['spy_weight'])
    spy_sector = spy_clean.groupby('sector').apply(
        lambda g: pd.Series({
            'bench_w': g['spy_weight'].sum(),
            'bench_r': (g['spy_weight'] * g['tr_ytd']).sum() / g['spy_weight'].sum(),
            'spy_n': len(g)
        }), include_groups=False
    ).reset_index()

    merged = port_sector.merge(spy_sector, on='sector', how='outer').fillna(0)
    merged['active_w_mv'] = merged['port_w_mv'] - merged['bench_w']
    merged = merged.sort_values('active_w_mv', ascending=False)

    cols = ['sector', 'port_w_mv', 'bench_w', 'active_w_mv', 'port_r', 'bench_r', 'n', 'spy_n']
    out = merged[cols].copy()
    out['port_w_mv'] = out['port_w_mv'].apply(lambda x: f'{x*100:6.2f}%')
    out['bench_w'] = out['bench_w'].apply(lambda x: f'{x*100:6.2f}%')
    out['active_w_mv'] = out['active_w_mv'].apply(lambda x: f'{x*100:+6.2f}%')
    out['port_r'] = out['port_r'].apply(lambda x: f'{x*100:+7.2f}%' if pd.notna(x) and x != 0 else '    n/a')
    out['bench_r'] = out['bench_r'].apply(lambda x: f'{x*100:+7.2f}%' if pd.notna(x) and x != 0 else '    n/a')
    out.columns = ['Sector', 'PortW(MV)', 'BenchW', 'ActiveW', 'PortR', 'BenchR', '#Port', '#SPY']
    print(out.to_string(index=False))
    print()
    print('  [註] PortW(MV) = 期末市值權重 (報表口徑)；歸因表使用 cost-based 權重以保證 reconcile。')

    return merged


# =============================================================
# 5. Brinson-Fachler attribution
# =============================================================
def section_attribution(d, sector_table):
    hr('4. Brinson-Fachler 歸因')
    # Total benchmark return: 用 SPY 實際 YTD (非 cap-weighted 自算, 避免成分股替換造成 drift)
    r_b_total = d['totals']['spy_ytd']
    # Total portfolio return (cost-based, matches 簡單法)
    r_p_total = d['totals']['pnl_end'] / d['totals']['cost_end']

    df = sector_table.copy()
    # 使用 cost-based 權重 (port_w), 與 cost-based return (port_r) 配對, 確保 Σ w·r = 簡單法總報酬
    is_off = df['sector'] == 'Off-Benchmark'

    # Brinson-Fachler
    # Allocation: (w_p - w_b) × (r_b_sector - r_b_total)
    # Selection : w_b × (r_p_sector - r_b_sector)
    # Interaction: (w_p - w_b) × (r_p_sector - r_b_sector)
    # 標準 Brinson-Fachler 公式
    df['port_r_f'] = df['port_r'].fillna(0)  # 沒持有的 sector port_r 設 0
    df['alloc'] = (df['port_w'] - df['bench_w']) * (df['bench_r'] - r_b_total)
    df['select'] = df['bench_w'] * (df['port_r_f'] - df['bench_r'])
    df['interact'] = (df['port_w'] - df['bench_w']) * (df['port_r_f'] - df['bench_r'])

    # Off-Benchmark: w_b=0. 標準公式 (r_b_off=0 隱含):
    #   alloc = w_p × (0 - r_b_total) = -w_p × r_b_total
    #   sel = 0
    #   int = w_p × port_r → 已可表達
    # 但為了讓 Off-Bench alloc 反映「投資於 SPY 外資產」這個決策, 重新分配:
    #   alloc = w_p × (port_r - r_b_total)  ← 對 SPY 的 active 貢獻
    #   sel, int = 0
    # 注意: 此選擇造成 Σ effects = active_return - closed_only_pnl/cost_total - off_bench_residual
    # 殘差會在對帳區明確標示
    df.loc[is_off, 'alloc'] = df.loc[is_off, 'port_w'] * (df.loc[is_off, 'port_r_f'] - r_b_total)
    df.loc[is_off, 'select'] = 0.0
    df.loc[is_off, 'interact'] = 0.0

    df['total'] = df['alloc'] + df['select'] + df['interact']
    df = df.sort_values('total', ascending=False)

    # 計算殘差: closed-only sectors 的 P&L 未被 Brinson 捕捉
    cost_total = d['totals']['cost_end']
    closed_only_mask = (df['port_w'] == 0) & (df['pnl'] != 0) & ~is_off
    closed_only_residual = (df.loc[closed_only_mask, 'pnl'] / cost_total).sum()
    closed_only_sectors = df.loc[closed_only_mask, 'sector'].tolist()

    out = df[['sector', 'port_w', 'bench_w', 'port_r', 'bench_r',
              'alloc', 'select', 'interact', 'total']].copy()
    fmt_pct = lambda x: f'{x*100:+7.3f}%' if pd.notna(x) and x != 0 else '   0.000%'
    fmt_w = lambda x: f'{x*100:6.2f}%'
    out['port_w'] = out['port_w'].apply(fmt_w)
    out['bench_w'] = out['bench_w'].apply(fmt_w)
    out['port_r'] = out['port_r'].apply(lambda x: f'{x*100:+7.2f}%' if pd.notna(x) and x != 0 else '    n/a')
    out['bench_r'] = out['bench_r'].apply(lambda x: f'{x*100:+7.2f}%' if pd.notna(x) and x != 0 else '    n/a')
    out['alloc'] = out['alloc'].apply(fmt_pct)
    out['select'] = out['select'].apply(fmt_pct)
    out['interact'] = out['interact'].apply(fmt_pct)
    out['total'] = out['total'].apply(fmt_pct)
    out.columns = ['Sector', 'wP(cost)', 'wB', 'rP', 'rB',
                   'Alloc', 'Select', 'Interact', 'TotalActive']
    print(out.to_string(index=False))

    sub('合計 (對帳)')
    a = df['alloc'].sum()
    s = df['select'].sum()
    i = df['interact'].sum()
    sum_eff = a + s + i
    actual_active = r_p_total - r_b_total

    # SPY 成分股 reconstitution drift:
    # 期間 SPY 有成分股調整, 故當前成分股 cap-weighted Σ(w·YTD) 與實際 SPY YTD 不一致.
    # Brinson 公式內部以 cap-weighted bench_r_sec 推導, 對帳到 cap-weighted 總和;
    # 實際 SPY 較低 → 換成實際 SPY 為基準時, 組合 Active 變得更大, drift 為正值加項.
    spy_clean_w = d['spy'].dropna(subset=['spy_weight', 'tr_ytd'])
    r_b_capweighted = (spy_clean_w['spy_weight'] * spy_clean_w['tr_ytd']).sum() / spy_clean_w['spy_weight'].sum()
    reconstitution_drift = r_b_capweighted - r_b_total  # 正值 = cap-weighted 高估 SPY

    print(f"  Allocation Effect  (產業 tilt)   : {pct(a, 3)}")
    print(f"  Selection Effect   (個股選擇)    : {pct(s, 3)}")
    print(f"  Interaction Effect               : {pct(i, 3)}")
    print(f"  Closed-Only Sectors Residual     : {pct(closed_only_residual, 3)}  ({', '.join(closed_only_sectors)})")
    print(f"  Reconstitution Drift (+)         : {pct(reconstitution_drift, 3)}  (=r_b_capweighted−r_b_actual)")
    print(f"  歸因總和 + 殘差 + Drift          : {pct(sum_eff + closed_only_residual + reconstitution_drift, 3)}")
    print(f"  --")
    print(f"  Portfolio Return (簡單法)        : {pct(r_p_total, 3)}")
    print(f"  Benchmark Return (SPY 實際 YTD)  : {pct(r_b_total, 3)}")
    print(f"  Benchmark Return (成分股cap-wt)  : {pct(r_b_capweighted, 3)}  (僅供 Brinson 公式內部 r_b_sec 用)")
    print(f"  Active Return (vs SPY 實際)      : {pct(actual_active, 3)}")
    print(f"  Reconciliation Gap               : {pct(actual_active - sum_eff - closed_only_residual - reconstitution_drift, 3)}  (應接近 0)")
    print()
    print("  [口徑] 權重: 期末庫存成本基礎 (Σ cost_i / total_cost) — 與簡單法報酬一致")
    print("         報酬: P&L / cost_basis_end (與報表 損益貢獻% 同口徑)")
    print("         r_b_total 用 SPY 實際 YTD; 各 sector r_b 仍為當期成分股 cap-weighted")
    print("         Reconstitution Drift = 期間 SPY 成分股增減造成的 cap-weighted 與實際 SPY 之差")
    print("         Closed-Only Sectors 殘差: 該 sector 已全部平倉, P&L 未進入 Brinson 公式")

    return df, {
        'alloc': a,
        'select': s,
        'interact': i,
        'closed_residual': closed_only_residual,
        'closed_sectors': closed_only_sectors,
        'r_b_total': r_b_total,           # 實際 SPY YTD (Bloomberg)
        'r_b_capweighted': r_b_capweighted,  # 自算成分股 cap-weighted (僅供參考)
        'reconstitution_drift': reconstitution_drift,
        'r_p_total': r_p_total,
        'actual_active': actual_active,
    }


# =============================================================
# 6. Trade analysis
# =============================================================
def section_trades(d):
    hr('5. 交易分析')
    tr = d['tr']

    buys = tr[tr['side'] == '買']
    sells = tr[tr['side'] == '賣']

    sub('交易筆數與金額')
    print(f"  買進筆數              : {len(buys)}     金額: {usd(buys['amount'].sum())}")
    print(f"  賣出筆數              : {len(sells)}      金額: {usd(sells['amount'].sum())}")
    print(f"  總交易筆數            : {len(tr)}")
    print(f"  涉及股票數            : {tr['ticker'].nunique()}")
    print(f"  日期範圍              : {tr['交易日期'].min().date()} ~ {tr['交易日期'].max().date()}")

    sub('週轉率 (Turnover)')
    avg_mv = (d['totals']['mv_start'] + d['totals']['mv_end']) / 2
    one_way = min(buys['amount'].sum(), sells['amount'].sum()) / avg_mv
    print(f"  平均 MV              : {usd(avg_mv)}")
    print(f"  One-way Turnover     : {pct(one_way)}  (min(買,賣)/avg MV)")
    print(f"  期間 (142天)，年化   : {pct(one_way * 365 / 142)}")

    sub('賣出單筆勝率 (依 價差損益)')
    # realized_pnl 為每筆賣出對應的價差損益
    sells_with_pnl = sells[sells['realized_pnl'].notna() & (sells['realized_pnl'] != 0)]
    winners = sells_with_pnl[sells_with_pnl['realized_pnl'] > 0]
    losers = sells_with_pnl[sells_with_pnl['realized_pnl'] < 0]
    print(f"  有實現損益的賣出筆數  : {len(sells_with_pnl)}")
    print(f"  獲利筆數              : {len(winners)}  ({len(winners)/max(len(sells_with_pnl),1)*100:.1f}%)")
    print(f"  虧損筆數              : {len(losers)}   ({len(losers)/max(len(sells_with_pnl),1)*100:.1f}%)")
    avg_win = winners['realized_pnl'].mean() if len(winners) > 0 else 0
    avg_loss = losers['realized_pnl'].mean() if len(losers) > 0 else 0
    print(f"  平均獲利 / 筆         : {usd(avg_win)}")
    print(f"  平均虧損 / 筆         : {usd(avg_loss)}")
    print(f"  獲利/虧損比 (P/L)     : {abs(avg_win/avg_loss):.2f}x" if avg_loss else "  N/A")
    print(f"  合計實現損益          : {usd(sells_with_pnl['realized_pnl'].sum())}")

    sub('Round-trip 持有期 (完整買賣已結束的部位)')
    # 用 5/21 庫存=0 但 12/31 有持有 或 期間有交易過的部位
    closed = []
    for tk, g in tr.groupby('ticker'):
        net_qty = g['signed_qty'].sum()
        if abs(net_qty) < 1 and g['side'].nunique() > 1:
            first_buy = g[g['side'] == '買']['交易日期'].min()
            last_sell = g[g['side'] == '賣']['交易日期'].max()
            if pd.notna(first_buy) and pd.notna(last_sell):
                closed.append({
                    'ticker': tk,
                    'days': (last_sell - first_buy).days,
                    'pnl': g[g['side']=='賣']['realized_pnl'].sum()
                })
    if closed:
        cdf = pd.DataFrame(closed).sort_values('days')
        print(f"  完整 round-trip 部位數: {len(cdf)}")
        print(f"  平均持有天數          : {cdf['days'].mean():.0f}")
        print(f"  中位數持有天數        : {cdf['days'].median():.0f}")
        print(f"  最短/最長             : {cdf['days'].min()} / {cdf['days'].max()}")
        print(f"  Round-trip 實現損益   : {usd(cdf['pnl'].sum())}")
        print('  Round-trips:')
        print(cdf.to_string(index=False))

    sub('月度交易節奏')
    tr['month'] = tr['交易日期'].dt.to_period('M')
    monthly = tr.groupby(['month', 'side']).agg(
        n=('amount', 'count'),
        amount=('amount', 'sum')
    ).unstack(fill_value=0)
    print(monthly.to_string())


# =============================================================
# 7. Quant edge
# =============================================================
def section_quant_edge(d):
    hr('6. 量化模型 Edge 分析')
    h5 = d['h5']
    spy = d['spy']

    held = h5[h5['市值'] > 0].copy()
    closed = h5[h5['市值'] == 0].copy()

    # ----- Hit rate -----
    sub('命中率 (Hit Rate)')
    profitable_held = (held['P&L(YTD)'] > 0).sum()
    profitable_all = (h5['P&L(YTD)'] > 0).sum()
    print(f"  在倉部位 (5/21)       : {len(held)}     獲利: {profitable_held}  ({profitable_held/len(held)*100:.1f}%)")
    print(f"  期內所有經手部位      : {len(h5)}     獲利: {profitable_all}  ({profitable_all/len(h5)*100:.1f}%)")

    # ----- Held vs SPY constituents (within-SPY comparison) -----
    sub('持股 vs SPY 母體 (僅 SPY 成分股)')
    held_in_spy = held.merge(spy[['ticker', 'name_spy', 'sector', 'spy_weight', 'tr_ytd']], on='ticker', how='inner')
    spy_clean = spy.dropna(subset=['tr_ytd'])

    spy_tr_mean_uw = spy_clean['tr_ytd'].mean()
    spy_actual = d['totals']['spy_ytd']  # 用 Bloomberg 實際 SPY YTD (非自算 cap-weighted)
    held_tr_mean_uw = held_in_spy['tr_ytd'].mean()
    print(f"  SPY 母體平均 YTD% (等權)      : {pct(spy_tr_mean_uw)}")
    print(f"  SPY 實際 YTD% (Bloomberg)    : {pct(spy_actual)}")
    print(f"  持股 SPY 部分平均 YTD% (等權) : {pct(held_tr_mean_uw)}")
    print(f"  持股相對 SPY 母體的超額 (等權): {pct(held_tr_mean_uw - spy_tr_mean_uw)}")

    # Percentile rank of each holding within SPY
    held_in_spy['spy_pct_rank'] = held_in_spy['tr_ytd'].rank(pct=True) * 100
    # rank within full SPY
    spy_sorted = spy_clean.sort_values('tr_ytd').reset_index(drop=True)
    spy_sorted['rank_full'] = spy_sorted['tr_ytd'].rank(pct=True) * 100
    held_in_spy = held_in_spy.merge(
        spy_sorted[['ticker', 'rank_full']], on='ticker', how='left'
    )

    sub('在倉部位於 SPY 母體的 YTD% 百分位 (越高越好)')
    rank_df = held_in_spy[['ticker', '公司名稱', '權重', 'P&L(YTD)', 'tr_ytd', 'rank_full']]
    rank_df = rank_df.sort_values('rank_full', ascending=False)
    rank_df = rank_df.rename(columns={'tr_ytd': 'YTD%', 'rank_full': 'SPY_Pct'})
    rank_df['權重'] = rank_df['權重'].apply(lambda x: f'{x*100:5.2f}%')
    rank_df['YTD%'] = rank_df['YTD%'].apply(lambda x: f'{x*100:+7.2f}%')
    rank_df['SPY_Pct'] = rank_df['SPY_Pct'].apply(lambda x: f'{x:5.1f}')
    rank_df['P&L(YTD)'] = rank_df['P&L(YTD)'].apply(lambda x: f'{x:,.0f}')
    print(rank_df.to_string(index=False))

    sub('在倉持股 vs SPY 母體分位數摘要 (僅 SPY 成分)')
    mean_pct = held_in_spy['rank_full'].mean()
    pct_above_50 = (held_in_spy['rank_full'] > 50).sum() / len(held_in_spy) * 100
    pct_top_quartile = (held_in_spy['rank_full'] > 75).sum() / len(held_in_spy) * 100
    print(f"  在倉持股平均 SPY 分位數 : {mean_pct:.1f}  (50=隨機選股)")
    print(f"  落在 SPY 上半 (>50)     : {pct_above_50:.1f}%")
    print(f"  落在 SPY 前 1/4 (>75)   : {pct_top_quartile:.1f}%")

    # ----- Top SPY constituents we missed -----
    sub('SPY 內前 10 大漲幅但組合未持有')
    spy_with_weight = spy_clean[spy_clean['spy_weight'] > 0].copy()
    held_set = set(held['ticker'])
    missed = spy_with_weight[~spy_with_weight['ticker'].isin(held_set)]
    missed_top = missed.nlargest(10, 'tr_ytd')[['ticker', 'name_spy', 'sector', 'spy_weight', 'tr_ytd']]
    missed_top['spy_weight'] = missed_top['spy_weight'].apply(lambda x: f'{x*100:.3f}%')
    missed_top['tr_ytd'] = missed_top['tr_ytd'].apply(lambda x: f'{x*100:+.2f}%')
    print(missed_top.to_string(index=False))

    sub('組合未持但落在 SPY 後段, 確認避開的負貢獻')
    spy_with_weight['port_held'] = spy_with_weight['ticker'].isin(held_set)
    not_held_bottom = spy_with_weight[~spy_with_weight['port_held']].nsmallest(10, 'tr_ytd')[
        ['ticker', 'name_spy', 'sector', 'spy_weight', 'tr_ytd']
    ]
    not_held_bottom['spy_weight'] = not_held_bottom['spy_weight'].apply(lambda x: f'{x*100:.3f}%')
    not_held_bottom['tr_ytd'] = not_held_bottom['tr_ytd'].apply(lambda x: f'{x*100:+.2f}%')
    print(not_held_bottom.to_string(index=False))

    # ----- Off-Benchmark contribution -----
    sub('Off-Benchmark 持股表現 (5 檔 不在 SPY 內)')
    off_bench = held.merge(spy[['ticker', 'sector']], on='ticker', how='left')
    off_bench = off_bench[off_bench['sector'].isna()].copy()
    if len(off_bench) > 0:
        off_bench['return'] = off_bench['P&L(YTD)'] / off_bench['庫存成本(YTD)']
        print(off_bench[['ticker', '公司名稱', '市值', '權重', 'P&L(YTD)', 'return']].to_string(index=False))
        total_pnl = h5['P&L(YTD)'].sum()
        off_pnl = off_bench['P&L(YTD)'].sum()
        print(f"\n  Off-Bench 累計 P&L     : {usd(off_pnl)}")
        print(f"  佔總 P&L 比             : {pct(off_pnl/total_pnl)}")

    return {
        'held_in_spy': held_in_spy.sort_values('rank_full', ascending=False),
        'n_held': len(held),
        'n_all': len(h5),
        'profitable_held': int(profitable_held),
        'profitable_all': int(profitable_all),
        'hit_rate_held': profitable_held / len(held),
        'hit_rate_all': profitable_all / len(h5),
        'spy_avg_uw': spy_tr_mean_uw,
        'spy_avg_cw': spy_actual,  # 此鍵保留以避免下游 break, 但語意改為「SPY 實際 YTD」
        'spy_actual': spy_actual,
        'port_avg_uw': held_tr_mean_uw,
        'mean_pct': mean_pct,
        'pct_above_50': pct_above_50,
        'pct_top25': pct_top_quartile,
        'spy_clean': spy_clean,
        'missed_top': missed.nlargest(10, 'tr_ytd')[['ticker', 'name_spy', 'sector', 'spy_weight', 'tr_ytd']],
        'not_held_bottom': spy_with_weight[~spy_with_weight['port_held']].nsmallest(10, 'tr_ytd')[
            ['ticker', 'name_spy', 'sector', 'spy_weight', 'tr_ytd']
        ],
        'off_bench': off_bench,
    }


# =============================================================
# Main
# =============================================================
def main():
    print(f"\n  量化持股組合分析  |  生成時間: {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  期間: {PERIOD_START.date()} → {PERIOD_END.date()}  ({DAYS} 天)")
    print(f"  資料: {FP.name}")

    d = load_data(FP)
    results = {}
    results['perf'] = section_performance(d)
    results['holdings'] = section_holdings(d)
    sector_table = section_sector(d)
    results['sector_df'] = sector_table
    attr_df, attr_summary = section_attribution(d, sector_table)
    results['attr_df'] = attr_df
    results['attr_summary'] = attr_summary
    section_trades(d)  # console only — HTML uses raw tr
    results['quant'] = section_quant_edge(d)

    print('\n' + '=' * 78)
    print('  Console 分析完成, 開始產出 HTML 圖表報告...')
    print('=' * 78)

    # 產出 HTML 報告: 使用本次 console 已算出的 results, 不重算
    from generate_html_report import build_html, OUTPUT_HTML
    html = build_html(d, results)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'  ✓ HTML 報告已產出: {OUTPUT_HTML.name}  ({OUTPUT_HTML.stat().st_size/1024:.1f} KB)')
    print(f'  路徑: {OUTPUT_HTML}')
    print('=' * 78 + '\n')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
