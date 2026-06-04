# -*- coding: utf-8 -*-
"""
量化持股組合分析  (2025-12-31 → 2026-05-29)

資料來源: 計量績效分析.xlsx
  - 持股: 每日持股快照 (DATE_ × ticker)
  - 交易: 期內所有交易明細 (44 欄)
  - Bloomberg歸因分析: Bloomberg 提供的 GICS 歸因 (因子→產業報酬, 個股選擇; 時間報酬於載入時移除)
  - Note: SQL 註解

輸出: console + 績效分析報告.html (tab UI)
"""
import sys, io
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

pd.set_option('display.float_format', lambda x: f'{x:,.4f}' if abs(x) < 1 else f'{x:,.2f}')
pd.set_option('display.width', 200)
pd.set_option('display.max_columns', None)

FP = Path(__file__).parent / '計量績效分析.xlsx'

# 簡單法分母: 固定 USD 665M (核給額度)
SIMPLE_RETURN_DENOMINATOR = 665_000_000

# 11 個 GICS sectors (Bloomberg 中文)
SECTORS_GICS = ['通訊服務', '非核心消費', '核心消費', '能源', '金融',
                '醫療保健', '工業', '資訊技術', '原材料', '房地產', '公用事業']

# 中英對照 (用於 fallback / 標準化)
SECTOR_EN_MAP = {
    '通訊服務': 'Communication Services', '非核心消費': 'Consumer Discretionary',
    '核心消費': 'Consumer Staples', '能源': 'Energy', '金融': 'Financials',
    '醫療保健': 'Health Care', '工業': 'Industrials', '資訊技術': 'Information Technology',
    '原材料': 'Materials', '房地產': 'Real Estate', '公用事業': 'Utilities',
}


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
    return str(t).strip().replace(' Equity', '')

def parse_date_int(d):
    """20251231 -> Timestamp(2025-12-31)"""
    if pd.isna(d): return None
    return pd.Timestamp(str(int(d)))


# =============================================================
# 1. Data loader
# =============================================================
def load_data(fp):
    # ---- 持股 (daily snapshots) ----
    holdings = pd.read_excel(fp, sheet_name='持股')
    holdings['DATE_'] = holdings['DATE_'].apply(parse_date_int)
    holdings['ticker'] = holdings['BBG_TICKER'].apply(norm_ticker)
    for c in ['TOTAL_SHARES', 'TOTAL_COST', 'AVG_UNIT_COST', 'CLS_PRICE',
              'TOTAL_MV', 'TOTAL_URCG', 'TOTAL_DVD', 'TOTAL_REALIZED', 'TOTAL_PL']:
        holdings[c] = pd.to_numeric(holdings[c], errors='coerce')

    dates = sorted(holdings['DATE_'].unique())
    period_start = dates[0]
    period_end = dates[-1]

    # ---- 交易 ----
    tr = pd.read_excel(fp, sheet_name='交易')
    tr['交易日期'] = pd.to_datetime(tr['交易日期'])
    tr['ticker'] = tr['股票代碼(代碼)'].apply(norm_ticker)
    tr = tr.rename(columns={
        '交易型態': 'side',
        '股數': 'qty',
        '單價(原幣)': 'price',
        '成交金額(原幣)': 'gross_amount',
        '交易淨額(原幣)': 'amount',
        '平均成本(原幣)': 'avg_cost',
        '交易成本(原幣)': 'cost_basis',
        '價差損益(原幣)': 'realized_pnl',
        '交易手續費(原幣)': 'commission',
        '交易稅(原幣)': 'tax',
        '其他費用(原幣)': 'other_fee',
    })
    for c in ['qty', 'price', 'gross_amount', 'amount', 'avg_cost', 'cost_basis',
              'realized_pnl', 'commission', 'tax', 'other_fee']:
        if c in tr.columns:
            tr[c] = pd.to_numeric(tr[c], errors='coerce')
    tr['signed_qty'] = np.where(tr['side'] == '買', tr['qty'], -tr['qty'])
    tr['signed_amount'] = np.where(tr['side'] == '買', tr['amount'], -tr['amount'])

    # ---- Bloomberg 歸因分析 ----
    bb_raw = pd.read_excel(fp, sheet_name='Bloomberg歸因分析', header=None)
    # 結構: row 0-7 為 metadata, row 8-9 為 multi-level header, row 10+ 為資料
    # 載入時就把「因子報酬」改名為「產業報酬」(industry_*), 並移除「時間報酬」(time_*) 欄位
    BB_COLS = [
        'name',                       # 名稱 (sector/security)
        'wt_port', 'wt_bench', 'wt_active',
        'tr_port', 'tr_bench', 'tr_active',
        'industry_port', 'industry_bench', 'industry_active',  # 原「因子報酬」
        'sel_port', 'sel_bench', 'sel_active',
        '_time_port', '_time_bench', '_time_active',           # 「時間報酬」載入後立即丟棄
        'ctr_port', 'ctr_bench', 'ctr_active',
    ]
    bb = bb_raw.iloc[10:, 1:20].copy()  # 從 row 10 開始, col 1-19 (跳過 col 0 NaN)
    bb.columns = BB_COLS
    bb = bb.dropna(subset=['name']).reset_index(drop=True)
    # 將數值欄轉成 float
    for c in BB_COLS[1:]:
        bb[c] = pd.to_numeric(bb[c], errors='coerce')
    # 移除時間報酬欄位 (本報告不使用 Timing)
    bb = bb.drop(columns=['_time_port', '_time_bench', '_time_active'])

    # 識別 sector vs security: 用 sector 中文名單比對
    bb['is_sector'] = bb['name'].isin(SECTORS_GICS)

    # 為每個 security 標記其所屬 sector (向下 forward fill)
    bb['sector'] = np.where(bb['is_sector'], bb['name'], np.nan)
    bb['sector'] = bb['sector'].ffill()

    # 拆成 4 個子集: totals (Holdings/Residuals), sectors, securities
    bb_holdings = bb[bb['name'] == 'Holdings'].iloc[0] if (bb['name'] == 'Holdings').any() else None
    bb_residuals = bb[bb['name'] == 'Residuals'].iloc[0] if (bb['name'] == 'Residuals').any() else None
    bb_sectors = bb[bb['is_sector']].copy()
    bb_securities = bb[~bb['is_sector'] & ~bb['name'].isin(['Holdings', 'Residuals', 'QUANT'])].copy()
    # security 列要排除 sector 列自己 — is_sector 已排除
    bb_securities = bb_securities[bb_securities['sector'].notna()].copy()

    # ---- Totals (from Bloomberg + daily holdings) ----
    # daily totals (group by date)
    daily = holdings.groupby('DATE_').agg(
        mv=('TOTAL_MV', 'sum'),
        cost=('TOTAL_COST', 'sum'),
        urcg=('TOTAL_URCG', 'sum'),
        dvd=('TOTAL_DVD', 'sum'),
        realized=('TOTAL_REALIZED', 'sum'),
        pnl=('TOTAL_PL', 'sum'),
        n_holdings=('TOTAL_MV', lambda x: (x > 0).sum()),
    ).reset_index().sort_values('DATE_').reset_index(drop=True)

    h_start = holdings[holdings['DATE_'] == period_start]
    h_end = holdings[holdings['DATE_'] == period_end]
    mv_start = h_start['TOTAL_MV'].sum()
    mv_end = h_end['TOTAL_MV'].sum()
    days = (period_end - period_start).days

    # Modified Dietz (自算 TWRR 近似): 將每筆交易視為對 stock-portfolio 帳戶的外部 CF
    cfs = tr.copy()
    cfs['days_since_start'] = (cfs['交易日期'] - period_start).dt.days
    cfs['weight_md'] = (days - cfs['days_since_start']) / days
    cfs['weighted_cf'] = cfs['signed_amount'] * cfs['weight_md']
    net_cf = cfs['signed_amount'].sum()
    weighted_cf = cfs['weighted_cf'].sum()
    md_denom = mv_start + weighted_cf
    md_return = (mv_end - mv_start - net_cf) / md_denom if md_denom else None

    # Active Share = ½ × Σ |w_port - w_bench| (from Bloomberg security-level data)
    bb_secs_all = bb[~bb['is_sector'] & ~bb['name'].isin(['Holdings', 'Residuals', 'QUANT'])]
    active_share = bb_secs_all['wt_active'].abs().sum() / 2 / 100  # Bloomberg 值為 % 形式

    totals = {
        'period_start': period_start,
        'period_end': period_end,
        'days': days,
        'mv_start': mv_start,
        'mv_end': mv_end,
        'cost_start': h_start['TOTAL_COST'].sum(),
        'cost_end': h_end['TOTAL_COST'].sum(),
        'pnl_start': h_start['TOTAL_PL'].sum(),
        'pnl_end': h_end['TOTAL_PL'].sum(),
        'urcg_end': h_end['TOTAL_URCG'].sum(),
        'realized_end': h_end['TOTAL_REALIZED'].sum(),
        'dvd_end': h_end['TOTAL_DVD'].sum(),
        'net_cf': net_cf,
        'md_return': md_return,
        'active_share': active_share,
        # Bloomberg authoritative numbers
        'bb_port_return': bb_holdings['tr_port'] / 100 if bb_holdings is not None else None,
        'bb_bench_return': bb_holdings['tr_bench'] / 100 if bb_holdings is not None else None,
        'bb_active_return': bb_holdings['tr_active'] / 100 if bb_holdings is not None else None,
        'bb_industry_active': bb_holdings['industry_active'] / 100 if bb_holdings is not None else None,
        'bb_selection_active': bb_holdings['sel_active'] / 100 if bb_holdings is not None else None,
    }

    return {
        'holdings': holdings,
        'h_start': h_start, 'h_end': h_end,
        'daily': daily,
        'trades': tr,
        'bb': bb,
        'bb_holdings': bb_holdings,
        'bb_sectors': bb_sectors,
        'bb_securities': bb_securities,
        'totals': totals,
    }


# =============================================================
# 2. Performance summary
# =============================================================
def section_performance(d):
    t = d['totals']
    hr(f"1. 績效摘要 ({t['period_start'].date()} → {t['period_end'].date()}, {t['days']} 天)")

    print(f"期初 MV ({t['period_start'].date()}): {usd(t['mv_start'])}")
    print(f"期末 MV ({t['period_end'].date()}): {usd(t['mv_end'])}")
    print(f"期末庫存成本                  : {usd(t['cost_end'])}")
    print(f"期末總損益                    : {usd(t['pnl_end'])}")

    delta_cost = t['cost_end'] - t['cost_start']
    print(f"期內成本變動 (期末-期初)      : {usd(delta_cost)}")
    print(f"期內淨買進 (買-賣)            : {usd(t['net_cf'])}")
    print()

    simple_return = t['pnl_end'] / SIMPLE_RETURN_DENOMINATOR
    print(f"報酬率口徑:")
    print(f"  [A] 簡單法 (P&L/額度665M)    : {pct(simple_return)}    ← 核給額度口徑")
    print(f"  [B] Modified Dietz (自算)    : {pct(t['md_return'])}    ← 自算 TWRR 近似")
    print(f"  [C] Bloomberg TWRR (組合)    : {pct(t['bb_port_return'])}    ← Bloomberg 真實 TWRR")
    print(f"  [D] SPY (基準)               : {pct(t['bb_bench_return'])}")
    print()
    print(f"  Active Return (C - D)        : {pct(t['bb_active_return'])}")
    print(f"  Active Share                 : {pct(t['active_share'], 2)}    (½ × Σ|wP-wB|, Bloomberg sec-level)")
    print()
    print("  [說明] Bloomberg TWRR (C) 為每日真實時間加權, 與 SPY 同基準的標準口徑")
    print("         Modified Dietz (B) 是自算近似法, 應接近 (C)")

    return {
        'period_start': t['period_start'],
        'period_end': t['period_end'],
        'days': t['days'],
        'mv_start': t['mv_start'], 'mv_end': t['mv_end'],
        'cost_start': t['cost_start'], 'cost_end': t['cost_end'],
        'pnl_end': t['pnl_end'],
        'net_cf': t['net_cf'],
        'simple_return': simple_return,
        'md_return': t['md_return'],
        'port_return': t['bb_port_return'],
        'spy_return': t['bb_bench_return'],
        'active_return': t['bb_active_return'],
        'active_share': t['active_share'],
        'industry_active': t['bb_industry_active'],
        'selection_active': t['bb_selection_active'],
    }


# =============================================================
# 3. Holdings & contribution
# =============================================================
def section_holdings(d):
    hr('2. 持股與貢獻分析 (以期末快照為基準)')
    h_end = d['h_end'].copy()
    h_start = d['h_start'].copy()

    held = h_end[h_end['TOTAL_MV'] > 0].copy()
    closed_in_end = h_end[(h_end['TOTAL_MV'] == 0) & (h_end['TOTAL_PL'] != 0)].copy()
    held_start = h_start[h_start['TOTAL_MV'] > 0].copy()

    # ---- Top/Bottom contributors (期末快照) ----
    sub('Top 10 P&L 貢獻者')
    top10 = h_end.nlargest(10, 'TOTAL_PL')[['ticker', 'STK_NAME', 'TOTAL_URCG',
                                              'TOTAL_REALIZED', 'TOTAL_DVD', 'TOTAL_PL']].copy()
    print(top10.to_string(index=False))

    sub('Bottom 10 P&L 貢獻者')
    bot10 = h_end.nsmallest(10, 'TOTAL_PL')[['ticker', 'STK_NAME', 'TOTAL_URCG',
                                              'TOTAL_REALIZED', 'TOTAL_DVD', 'TOTAL_PL']].copy()
    print(bot10.to_string(index=False))

    # ---- P&L 結構 ----
    sub('P&L 結構分解')
    total_urcg = h_end['TOTAL_URCG'].sum()
    total_realized = h_end['TOTAL_REALIZED'].sum()
    total_dvd = h_end['TOTAL_DVD'].sum()
    total_pnl = h_end['TOTAL_PL'].sum()
    print(f"  未實現損益 (URCG)   : {usd(total_urcg):>20}   {pct(total_urcg/total_pnl if total_pnl else None)}")
    print(f"  已實現損益 (RCG)    : {usd(total_realized):>20}   {pct(total_realized/total_pnl if total_pnl else None)}")
    print(f"  股息收入   (DVD)    : {usd(total_dvd):>20}   {pct(total_dvd/total_pnl if total_pnl else None)}")
    print(f"  --")
    print(f"  總損益             : {usd(total_pnl):>20}   100.00%")

    # ---- 部位變化 ----
    sub('部位變化')
    set_start = set(held_start['ticker'])
    set_end = set(held['ticker'])
    continued = set_start & set_end
    new_pos = set_end - set_start
    sold_out = set_start - set_end
    all_touched = set(d['holdings']['ticker'].unique())
    print(f"  期初持倉檔數: {len(set_start)}     期末持倉檔數: {len(set_end)}")
    print(f"  └ 留任: {len(continued)}     └ 新進: {len(new_pos)}     └ 賣光: {len(sold_out)}")
    print(f"  期內所有出現過的 ticker (含已平倉): {len(all_touched)}")

    # ---- 集中度 ----
    sub('集中度')
    port_mv_total = held['TOTAL_MV'].sum()
    held['weight'] = held['TOTAL_MV'] / port_mv_total
    held_sorted = held.sort_values('weight', ascending=False)
    top5_w = held_sorted.head(5)['weight'].sum()
    top10_w = held_sorted.head(10)['weight'].sum()
    eff_n = 1 / (held['weight'] ** 2).sum()
    print(f"  Top  5 權重: {pct(top5_w)}")
    print(f"  Top 10 權重: {pct(top10_w)}")
    print(f"  有效持股數 (1/Σw²): {eff_n:.1f}")

    return {
        'held': held, 'held_sorted': held_sorted,
        'top10': top10, 'bot10': bot10,
        'pnl_decomp': {'urcg': total_urcg, 'rcg': total_realized,
                       'dvd': total_dvd, 'total': total_pnl},
        'n_continued': len(continued), 'n_new': len(new_pos), 'n_sold_out': len(sold_out),
        'n_held_start': len(set_start), 'n_held_end': len(set_end),
        'top5_w': top5_w, 'top10_w': top10_w, 'eff_n': eff_n,
    }


# =============================================================
# 4. 每日動態
# =============================================================
def section_daily(d):
    hr('3. 每日 MV / P&L 時間序列')
    daily = d['daily']
    print(f"資料點數: {len(daily)} 個交易日")
    print()
    print(daily.head(3).to_string(index=False))
    print('...')
    print(daily.tail(3).to_string(index=False))
    print()
    print(f"  最高 MV: {usd(daily['mv'].max())}  on {daily.loc[daily['mv'].idxmax(), 'DATE_'].date()}")
    print(f"  最低 MV: {usd(daily['mv'].min())}  on {daily.loc[daily['mv'].idxmin(), 'DATE_'].date()}")
    print(f"  最大持倉檔數: {int(daily['n_holdings'].max())}")
    print(f"  最小持倉檔數: {int(daily['n_holdings'].min())}")
    return {'daily': daily}


# =============================================================
# 5. Sector exposure (from Bloomberg)
# =============================================================
def section_sector(d):
    hr('4. 產業 (GICS) 曝險與報酬 (Bloomberg)')
    bb = d['bb_sectors'].copy()
    bb = bb.fillna({'wt_port': 0, 'tr_port': 0})
    bb = bb.sort_values('wt_active', ascending=False).reset_index(drop=True)
    # 顯示用子集
    out = bb[['name', 'wt_port', 'wt_bench', 'wt_active',
              'tr_port', 'tr_bench', 'tr_active']].copy()
    out.columns = ['Sector', 'wP%', 'wB%', 'wActive%', 'rP%', 'rB%', 'rActive%']
    for c in out.columns[1:]:
        out[c] = out[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) and x != 0 else '   0.00')
    print(out.to_string(index=False))
    print()
    print("  [註] wP/wB = 平均權重 (Bloomberg 期間平均, 非單日 snapshot)")
    print("        rP/rB = 期間總報酬, rActive = rP - rB")
    return bb  # 回傳完整 bb (含 industry/sel/ctr 等欄, 時間報酬已於載入時移除), 給 HTML 使用


# =============================================================
# 6. Brinson 歸因 (Bloomberg)
# =============================================================
def section_attribution(d):
    hr('5. Bloomberg 歸因分析 (Active 拆解: 產業報酬 + 個股選擇報酬)')
    t = d['totals']
    bb_sectors = d['bb_sectors'].copy()

    # Active = 產業報酬 + 個股選擇報酬 (Timing 已於資料載入時移除)
    print(f"  Active Return (Bloomberg)         : {pct(t['bb_active_return'])}")
    print(f"  ├─ 產業報酬 (industry)            : {pct(t['bb_industry_active'])}")
    print(f"  └─ 個股選擇報酬 (selection)       : {pct(t['bb_selection_active'])}")
    sum_check = (t['bb_industry_active'] or 0) + (t['bb_selection_active'] or 0)
    print(f"  Σ 兩項                            : {pct(sum_check)}")
    print()
    sub('各 Sector 對 Active 的貢獻 (% pts)')
    cols = ['name', 'wt_active', 'tr_active', 'industry_active', 'sel_active', 'ctr_active']
    df = bb_sectors[cols].copy().sort_values('ctr_active', ascending=False)
    df.columns = ['Sector', 'wActive%', 'rActive%', '產業報酬', '個股選擇', 'CTR']
    for c in df.columns[1:]:
        df[c] = df[c].apply(lambda x: f'{x:+7.3f}' if pd.notna(x) else '    n/a')
    print(df.to_string(index=False))
    print()
    print("  [說明] Bloomberg 採每日時間加權, 已內建處理成分股調整 / cash flow / 持倉變動.")
    print("         本報告於資料載入時移除「時間報酬」(Timing) 欄位; Active = 產業報酬 + 個股選擇報酬.")
    return bb_sectors


# =============================================================
# 7. 交易分析
# =============================================================
def section_trades(d):
    hr('6. 交易分析')
    tr = d['trades']
    buys = tr[tr['side'] == '買']
    sells = tr[tr['side'] == '賣']

    sub('交易筆數與金額 (原幣 USD)')
    print(f"  買進筆數: {len(buys):>4}   金額: {usd(buys['amount'].sum())}")
    print(f"  賣出筆數: {len(sells):>4}   金額: {usd(sells['amount'].sum())}")
    print(f"  總交易: {len(tr)}")
    print(f"  涉及股票: {tr['ticker'].nunique()}")
    print(f"  日期範圍: {tr['交易日期'].min().date()} ~ {tr['交易日期'].max().date()}")
    print(f"  總交易手續費: {usd(tr['commission'].sum())}")
    print(f"  總交易稅: {usd(tr['tax'].sum())}")
    print(f"  總其他費用: {usd(tr['other_fee'].sum())}")

    sub('週轉率')
    avg_mv = (d['totals']['mv_start'] + d['totals']['mv_end']) / 2
    one_way = min(buys['amount'].sum(), sells['amount'].sum()) / avg_mv if avg_mv else None
    print(f"  平均 MV: {usd(avg_mv)}")
    print(f"  One-way Turnover: {pct(one_way)}")
    days = d['totals']['days']
    print(f"  年化 (×365/{days}): {pct(one_way * 365 / days if one_way else None)}")

    sub('賣出單筆勝率 (依 價差損益)')
    sells_with_pnl = sells[sells['realized_pnl'].notna() & (sells['realized_pnl'] != 0)]
    winners = sells_with_pnl[sells_with_pnl['realized_pnl'] > 0]
    losers = sells_with_pnl[sells_with_pnl['realized_pnl'] < 0]
    n_sp = len(sells_with_pnl)
    print(f"  有實現損益筆數: {n_sp}   獲利: {len(winners)} ({len(winners)/max(n_sp,1)*100:.1f}%)   虧損: {len(losers)} ({len(losers)/max(n_sp,1)*100:.1f}%)")
    avg_win = winners['realized_pnl'].mean() if len(winners) else 0
    avg_loss = losers['realized_pnl'].mean() if len(losers) else 0
    print(f"  平均獲利/筆: {usd(avg_win)}")
    print(f"  平均虧損/筆: {usd(avg_loss)}")
    if avg_loss:
        print(f"  獲利/虧損比: {abs(avg_win/avg_loss):.2f}x")
    print(f"  合計實現損益: {usd(sells_with_pnl['realized_pnl'].sum())}")

    sub('月度交易節奏')
    tr2 = tr.copy()
    tr2['month'] = tr2['交易日期'].dt.to_period('M').astype(str)
    monthly = tr2.groupby(['month', 'side']).agg(
        n=('amount', 'count'),
        amount=('amount', 'sum'),
    ).unstack(fill_value=0)
    print(monthly.to_string())

    return {
        'buys': buys, 'sells': sells,
        'n_buys': len(buys), 'n_sells': len(sells),
        'amount_buy': buys['amount'].sum(),
        'amount_sell': sells['amount'].sum(),
        'avg_mv': avg_mv, 'one_way_turnover': one_way,
        'total_fees': tr['commission'].sum() + tr['tax'].sum() + tr['other_fee'].sum(),
        'sells_with_pnl': sells_with_pnl,
        'n_winners': len(winners), 'n_losers': len(losers),
        'avg_win': avg_win, 'avg_loss': avg_loss,
        'realized_sum': sells_with_pnl['realized_pnl'].sum(),
        'monthly': monthly,
    }


# =============================================================
# 8. 量化模型 Edge: 在倉持股 vs SPY 母體
# =============================================================
def section_quant_edge(d):
    hr('7. 量化模型 Edge: 在倉持股 vs SPY 母體 (Bloomberg 證券層, wt_port>0 且在 SPY 內)')
    bb_secs = d['bb_securities'].copy()

    # SPY 母體 (wt_bench > 0)
    spy_universe = bb_secs[bb_secs['wt_bench'].notna() & (bb_secs['wt_bench'] > 0)].copy()
    spy_universe = spy_universe.dropna(subset=['tr_bench']).reset_index(drop=True)
    spy_universe['rank_full'] = spy_universe['tr_bench'].rank(pct=True) * 100

    # 篩選: 投組平均權重 > 0 (Bloomberg wt_port > 0) 且 在 SPY 內 (wt_bench > 0)
    # Off-Benchmark (wt_bench=0/NaN) 自動排除
    held_in_spy = bb_secs[
        bb_secs['wt_port'].notna() & (bb_secs['wt_port'] > 0) &
        bb_secs['wt_bench'].notna() & (bb_secs['wt_bench'] > 0)
    ].copy()
    spy_rank_lookup = spy_universe[['name', 'tr_bench', 'rank_full']].copy()
    held_in_spy = held_in_spy.merge(spy_rank_lookup, on=['name', 'tr_bench'], how='left')
    n_held_in_spy = len(held_in_spy)

    print(f"  SPY 母體 (wt_bench > 0)               : {len(spy_universe)} 檔")
    print(f"  投組平均權重 > 0 且在 SPY 內           : {n_held_in_spy} 檔")
    print()

    if n_held_in_spy > 0:
        profitable = (held_in_spy['tr_port'] > 0).sum()
        hit_rate = profitable / n_held_in_spy
        mean_pct = held_in_spy['rank_full'].mean()
        pct_top25 = (held_in_spy['rank_full'] > 75).sum() / n_held_in_spy * 100
        pct_above_50 = (held_in_spy['rank_full'] > 50).sum() / n_held_in_spy * 100
        port_avg_uw = held_in_spy['tr_port'].mean()
    else:
        profitable = 0
        hit_rate = mean_pct = pct_top25 = pct_above_50 = port_avg_uw = None
    spy_avg_uw = spy_universe['tr_bench'].mean()

    sub('命中率 & SPY 百分位 (投組平均權重>0 ∩ SPY)')
    print(f"  命中率 (tr_port > 0)            : {int(profitable)} / {n_held_in_spy} = {pct(hit_rate, 1)}")
    print(f"  平均 SPY 百分位                 : {mean_pct:.1f}  (50 = 隨機選股)")
    print(f"  落在 SPY 上半 (>50)             : {pct_above_50:.1f}%")
    print(f"  落在 SPY 前 1/4 (>75)           : {pct_top25:.1f}%")
    print()
    print(f"  SPY 母體等權平均 YTD%           : {pct(spy_avg_uw/100 if spy_avg_uw else None)}")
    print(f"  持股 SPY 部分等權平均 YTD%      : {pct(port_avg_uw/100 if port_avg_uw else None)}")
    print(f"  超額 (等權)                     : {pct((port_avg_uw - spy_avg_uw)/100 if port_avg_uw and spy_avg_uw else None)}")

    sub(f'投組平均權重>0 部位於 SPY 母體的 YTD% 百分位 ({n_held_in_spy} 檔, sorted desc)')
    rank_df = held_in_spy[['name', 'sector', 'wt_port', 'tr_port', 'rank_full']].copy()
    rank_df = rank_df.sort_values('rank_full', ascending=False)
    for c in ['wt_port', 'tr_port', 'rank_full']:
        rank_df[c] = rank_df[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) else '   n/a')
    print(rank_df.to_string(index=False))

    # ----- 亮點分析: SPY Top N 強漲股 / Bottom N 弱跌股 捕捉率 -----
    held_names = set(held_in_spy['name'])
    spy_sorted_desc = spy_universe.sort_values('tr_bench', ascending=False).reset_index(drop=True)
    spy_sorted_asc = spy_universe.sort_values('tr_bench', ascending=True).reset_index(drop=True)

    bright = {}  # N -> {'hit_top', 'avoid_bottom'}
    for N in [10, 20, 50]:
        top_names = set(spy_sorted_desc.head(N)['name'])
        bot_names = set(spy_sorted_asc.head(N)['name'])
        bright[N] = {
            'top_names': top_names,
            'bot_names': bot_names,
            'held_in_top': len(held_names & top_names),
            'avoided_bottom': len(bot_names - held_names),
        }

    sub('量化模型亮點: 強漲股捕捉率 / 弱跌股迴避率')
    print(f"  {'目標':<25} {'命中持有':>10} {'迴避':>10}")
    for N in [10, 20, 50]:
        print(f"  SPY Top {N:<3} 漲幅 / Bottom {N:<3} 跌幅 :"
              f"   {bright[N]['held_in_top']:>2}/{N:<3}     {bright[N]['avoided_bottom']:>2}/{N:<3}")
    print()

    # 持有的 SPY Top 20 漲幅 (亮點 — 量化模型抓到的贏家)
    sub('★ 持有的 SPY Top 20 漲幅股 (量化模型亮點)')
    held_top20 = held_in_spy[held_in_spy['name'].isin(bright[20]['top_names'])].copy()
    held_top20 = held_top20.sort_values('tr_port', ascending=False)
    held_top20_disp = held_top20[['name', 'sector', 'wt_port', 'tr_port', 'rank_full']].copy()
    for c in ['wt_port', 'tr_port', 'rank_full']:
        held_top20_disp[c] = held_top20_disp[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) else '   n/a')
    if len(held_top20_disp) > 0:
        print(held_top20_disp.to_string(index=False))
    else:
        print('  (無)')

    # 避開的 SPY Bottom 20 跌幅
    sub('★ 避開的 SPY Bottom 20 跌幅股 (量化模型亮點)')
    avoided_bot20 = spy_universe[
        spy_universe['name'].isin(bright[20]['bot_names']) &
        ~spy_universe['name'].isin(held_names)
    ].copy().sort_values('tr_bench')
    avoided_bot20_disp = avoided_bot20[['name', 'sector', 'wt_bench', 'tr_bench', 'rank_full']].copy()
    for c in ['wt_bench', 'tr_bench', 'rank_full']:
        avoided_bot20_disp[c] = avoided_bot20_disp[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) else '   n/a')
    if len(avoided_bot20_disp) > 0:
        print(avoided_bot20_disp.to_string(index=False))
    else:
        print('  (無)')

    sub('SPY 內 Top 10 漲幅但組合未持有 (missed picks)')
    not_held_spy = spy_universe[~spy_universe['name'].isin(held_in_spy['name'])].copy()
    missed = not_held_spy.nlargest(10, 'tr_bench')[['name', 'sector', 'wt_bench', 'tr_bench', 'rank_full']]
    missed_disp = missed.copy()
    for c in ['wt_bench', 'tr_bench', 'rank_full']:
        missed_disp[c] = missed_disp[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) else '   n/a')
    print(missed_disp.to_string(index=False))

    sub('SPY 內 Bottom 10 跌幅但組合未持有 (correctly avoided)')
    avoided = not_held_spy.nsmallest(10, 'tr_bench')[['name', 'sector', 'wt_bench', 'tr_bench', 'rank_full']]
    avoided_disp = avoided.copy()
    for c in ['wt_bench', 'tr_bench', 'rank_full']:
        avoided_disp[c] = avoided_disp[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) else '   n/a')
    print(avoided_disp.to_string(index=False))

    return {
        'spy_universe': spy_universe,
        'held_in_spy': held_in_spy.sort_values('rank_full', ascending=False),
        'missed_top': missed,
        'avoided_bottom': avoided,
        'held_top20': held_top20,
        'avoided_bot20': avoided_bot20,
        'bright': {N: {
            'held_in_top': bright[N]['held_in_top'],
            'avoided_bottom': bright[N]['avoided_bottom'],
        } for N in [10, 20, 50]},
        'n_held_in_spy': n_held_in_spy,
        'n_profitable_in_spy': int(profitable),
        'hit_rate': hit_rate,
        'mean_pct': mean_pct,
        'pct_top25': pct_top25,
        'pct_above_50': pct_above_50,
        'port_avg_uw': port_avg_uw,
        'spy_avg_uw': spy_avg_uw,
    }


# =============================================================
# Main
# =============================================================
def main():
    print(f"\n  量化持股組合分析  |  生成時間: {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  資料: {FP.name}")

    d = load_data(FP)
    results = {}
    results['perf'] = section_performance(d)
    results['holdings'] = section_holdings(d)
    results['daily'] = section_daily(d)
    results['sector_df'] = section_sector(d)
    results['attribution'] = section_attribution(d)
    results['trades'] = section_trades(d)
    results['quant'] = section_quant_edge(d)

    print('\n' + '=' * 78)
    print('  Console 分析完成, 開始產出 HTML 圖表報告...')
    print('=' * 78)

    from generate_html_report import build_html, OUTPUT_HTML
    html = build_html(d, results)
    OUTPUT_HTML.write_text(html, encoding='utf-8')
    print(f'  ✓ HTML 報告已產出: {OUTPUT_HTML.name}  ({OUTPUT_HTML.stat().st_size/1024:.1f} KB)')
    print(f'  路徑: {OUTPUT_HTML}')
    print('=' * 78)

    # 自動開啟瀏覽器
    import webbrowser
    webbrowser.open(OUTPUT_HTML.as_uri())
    print(f'  → 已在預設瀏覽器開啟報表\n')


if __name__ == '__main__':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    main()
