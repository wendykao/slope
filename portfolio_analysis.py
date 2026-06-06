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

_ROOT = Path(__file__).parent
FP_HOLDINGS = _ROOT / '計量持股.xlsx'             # 持股 + 交易 (2 sheets)
FP_BB = _ROOT / 'Bloomberg歸因分析.xlsx'           # 每個 sheet 為一個期間的歸因
# 向後相容: 沿用舊變數名給其他地方引用
FP = FP_HOLDINGS

# ====================================================================
# 可調整參數 (修改這兩個變數即可改變整份報告的口徑與截止日)
# ====================================================================
QUOTA_USD = 665_000_000                          # 額度 (USD), 簡單法分母
REVIEW_DATE = pd.Timestamp('2026-05-21')         # 績效檢視日期 — 所有分析截止於此日

# 向後相容: 沿用舊變數名
SIMPLE_RETURN_DENOMINATOR = QUOTA_USD

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


def _normalize_bb_header(value):
    if pd.isna(value):
        return ''
    return (str(value).strip()
            .replace('\n', '')
            .replace(' ', '')
            .replace('　', '')
            .replace('％', '%')
            .replace('（', '(')
            .replace('）', ')')
            .casefold())


BB_NAME_HEADERS = {'name', '名稱'}
BB_CODE_HEADERS = {'代碼', '代码', 'ticker', 'code', '證券代碼', '证券代码'}
BB_PORT_HEADERS = ('投組', '投資組合', '組合', 'portfolio', 'port')
BB_BENCH_HEADERS = ('基準', 'benchmark', 'bench', 'bm')
BB_ACTIVE_HEADERS = ('活絡', '主動', 'active', '超額', '相對')

BB_REQUIRED_COLS = [
    'name',
    'wt_port', 'wt_bench', 'wt_active',
    'tr_port', 'tr_bench', 'tr_active',
    'industry_port', 'industry_bench', 'industry_active',
    'sel_port', 'sel_bench', 'sel_active',
    'ctr_port', 'ctr_bench', 'ctr_active',
]

BB_COLUMN_ORDER = [
    'name', 'code',
    'wt_port', 'wt_bench', 'wt_active',
    'end_wt_port', 'end_wt_bench', 'end_wt_active',
    'tr_port', 'tr_bench', 'tr_active',
    'industry_port', 'industry_bench', 'industry_active',
    'sel_port', 'sel_bench', 'sel_active',
    '_time_port', '_time_bench', '_time_active',
    'ctr_port', 'ctr_bench', 'ctr_active',
    'pnl_port', 'pnl_bench', 'pnl_active',
]


def _contains_any(text, keywords):
    return any(keyword in text for keyword in keywords)


def _resolve_bb_metric(header):
    if not header:
        return None
    if header in BB_NAME_HEADERS:
        return 'name'
    if header in BB_CODE_HEADERS:
        return 'code'
    if _contains_any(header, ('平均權重', 'averageweight', 'avgweight')):
        return 'wt'
    if (_contains_any(header, ('結束', '期末', 'ending', 'closing', 'end')) and
            _contains_any(header, ('權重', 'weight'))):
        return 'end_wt'
    if (_contains_any(header, ('因子', '產業', 'factor', 'industry')) and
            _contains_any(header, ('報酬', 'return'))):
        return 'industry'
    if (_contains_any(header, ('選擇', 'selection', 'select')) and
            _contains_any(header, ('報酬', 'return'))):
        return 'sel'
    if (_contains_any(header, ('時間', 'timing', 'time')) and
            _contains_any(header, ('報酬', 'return'))):
        return '_time'
    if _contains_any(header, ('ctr',)):
        return 'ctr'
    if _contains_any(header, ('損益', 'p/l', 'profit&loss', 'pnl')):
        return 'pnl'
    if ((_contains_any(header, ('貢獻', 'contribution')) and
         _contains_any(header, ('報酬率', '報酬', 'return'))) and
            not _contains_any(header, ('因子', '產業', 'factor', 'industry',
                                       '選擇', 'selection', 'select',
                                       '時間', 'timing', 'time'))):
        return 'ctr'
    if (_contains_any(header, ('總報酬', 'totalreturn')) or
            (_contains_any(header, ('報酬', 'return')) and
             not _contains_any(header, ('因子', '產業', 'factor', 'industry',
                                        '選擇', 'selection', 'select',
                                        '時間', 'timing', 'time',
                                        '貢獻', 'contribution', 'ctr')))):
        return 'tr'
    return None


def _resolve_bb_leg(header):
    if not header:
        return None
    if _contains_any(header, BB_PORT_HEADERS):
        return 'port'
    if _contains_any(header, BB_BENCH_HEADERS):
        return 'bench'
    if _contains_any(header, BB_ACTIVE_HEADERS):
        return 'active'
    return None


def _expand_bb_group_headers(top_header, sub_header):
    expanded = top_header.copy()
    last_top = ''
    for idx in range(len(expanded)):
        top_key = expanded.iloc[idx]
        if top_key:
            last_top = top_key
            continue
        if last_top and _resolve_bb_leg(sub_header.iloc[idx]):
            expanded.iloc[idx] = last_top
    return expanded


def _get_bb_column_map(bb_raw):
    header_row = None
    search_limit = min(25, max(len(bb_raw) - 1, 0))
    for idx in range(search_limit):
        row = bb_raw.iloc[idx].map(_normalize_bb_header)
        metrics = row.map(_resolve_bb_metric)
        if 'name' not in set(metrics.dropna()):
            continue
        if any(metric in {'wt', 'tr', 'industry', 'sel', 'ctr'} for metric in metrics.dropna()):
            header_row = idx
            break
    if header_row is None:
        raise ValueError('Bloomberg歸因分析: 找不到表頭列 (Name)。')

    top_header = bb_raw.iloc[header_row].map(_normalize_bb_header)
    sub_header = bb_raw.iloc[header_row + 1].map(_normalize_bb_header)
    top_header = _expand_bb_group_headers(top_header, sub_header)

    column_map = {}
    for col_idx in range(1, bb_raw.shape[1]):
        top_key = top_header.iloc[col_idx]
        sub_key = sub_header.iloc[col_idx]
        if not top_key and not sub_key:
            continue

        metric = _resolve_bb_metric(top_key)
        if metric in {'name', 'code'}:
            canonical = metric
        else:
            leg = _resolve_bb_leg(sub_key)
            canonical = f'{metric}_{leg}' if metric and leg else None

        if not canonical:
            continue
        if canonical in column_map:
            raise ValueError(f'Bloomberg歸因分析: 發現重複欄位 {canonical}')
        column_map[canonical] = col_idx

    missing = [col for col in BB_REQUIRED_COLS if col not in column_map]
    if missing:
        available = [
            f"{top_header.iloc[col_idx]}/{sub_header.iloc[col_idx]}"
            for col_idx in range(1, bb_raw.shape[1])
            if top_header.iloc[col_idx] or sub_header.iloc[col_idx]
        ]
        raise ValueError(
            f"Bloomberg歸因分析: 缺少必要欄位 {', '.join(missing)}; "
            f"已辨識表頭: {', '.join(available)}"
        )

    return header_row + 2, column_map


# =============================================================
# 1. Data loader
# =============================================================
def _pick_bb_sheet(fp_bb, target_date):
    """挑選與 target_date 最接近 (>=) 的 Bloomberg sheet (sheet 名格式 YYYYMMDD)"""
    xl = pd.ExcelFile(fp_bb)
    sheets = []
    for name in xl.sheet_names:
        try:
            sheets.append((pd.Timestamp(str(name)), name))
        except Exception:
            pass
    if not sheets:
        raise ValueError(f'{fp_bb.name}: 無法解析任何 sheet 名為日期')
    sheets.sort()
    target = pd.Timestamp(target_date)
    # 完全相等優先
    for d, name in sheets:
        if d == target:
            return name
    # 否則挑最接近 target 的
    nearest = min(sheets, key=lambda x: abs((x[0] - target).days))
    print(f'  [Bloomberg] period_end={target.date()} 無對應 sheet, 改用最近 {nearest[1]} ({nearest[0].date()})')
    return nearest[1]


def load_data(fp=None, *, fp_holdings=None, fp_bb=None, bb_sheet=None):
    """
    參數:
        fp_holdings: 計量持股.xlsx (含 持股 + 交易 兩 sheet)
        fp_bb: Bloomberg歸因分析.xlsx (多個期間 sheet, sheet 名格式 YYYYMMDD)
        bb_sheet: 指定 Bloomberg sheet 名; None 表示自動挑選與 period_end 對應的
        fp: 向後相容; 若給單一檔案, 兩種資料都從同一檔讀 (舊版單檔模式)
    """
    if fp_holdings is None:
        fp_holdings = fp if fp else FP_HOLDINGS
    if fp_bb is None:
        fp_bb = FP_BB if FP_BB.exists() else fp_holdings

    # ---- 持股 (daily snapshots) ----
    # 持股 sheet 有兩套 P&L/COST 欄位:
    #   TOTAL_*       : since-inception (持有以來累積)
    #   Reset_TOTAL_* : YTD reset (每年 1/1 歸零, 反映本年度損益)
    # 本報表所有期間分析都以 Reset 版本為準 → 直接覆寫 TOTAL_* 為 Reset_*.
    # 原始 since-inception 欄位保留為 _inception_TOTAL_*, 供需要時引用.
    holdings = pd.read_excel(fp_holdings, sheet_name='持股')
    holdings['DATE_'] = holdings['DATE_'].apply(parse_date_int)
    holdings['ticker'] = holdings['BBG_TICKER'].apply(norm_ticker)
    # 修正持股 sheet STK_NAME 佔位字
    STK_NAME_OVERRIDE = {
        'COHR US': '連貫公司',
    }
    holdings['STK_NAME'] = holdings.apply(
        lambda r: STK_NAME_OVERRIDE.get(r['BBG_TICKER'], r['STK_NAME']), axis=1
    )
    numeric_cols = ['TOTAL_SHARES', 'TOTAL_COST', 'AVG_UNIT_COST', 'CLS_PRICE',
                    'TOTAL_MV', 'TOTAL_URCG', 'TOTAL_DVD', 'TOTAL_REALIZED', 'TOTAL_PL',
                    'Reset_TOTAL_COST', 'Reset_AVG_UNIT_COST',
                    'Reset_TOTAL_URCG', 'Reset_TOTAL_REALIZED', 'Reset_TOTAL_PL']
    for c in numeric_cols:
        if c in holdings.columns:
            holdings[c] = pd.to_numeric(holdings[c], errors='coerce')

    # 用 Reset (YTD) 欄位覆寫 since-inception 欄位; 原欄位備份到 _inception_*
    RESET_MAP = {
        'Reset_TOTAL_COST': 'TOTAL_COST',
        'Reset_AVG_UNIT_COST': 'AVG_UNIT_COST',
        'Reset_TOTAL_URCG': 'TOTAL_URCG',
        'Reset_TOTAL_REALIZED': 'TOTAL_REALIZED',
        'Reset_TOTAL_PL': 'TOTAL_PL',
    }
    for reset_col, base_col in RESET_MAP.items():
        if reset_col in holdings.columns:
            holdings[f'_inception_{base_col}'] = holdings[base_col]
            holdings[base_col] = holdings[reset_col]
    # TOTAL_DVD 無 Reset 變種; Reset_PL 公式驗證: Reset_PL = Reset_URCG + Reset_REALIZED + TOTAL_DVD,
    # 表示 TOTAL_DVD 已隱含 YTD 口徑 (該年股息收入), 不需另外處理

    # 按 REVIEW_DATE 截斷; 取 <= REVIEW_DATE 的最大日期當期末
    holdings = holdings[holdings['DATE_'] <= REVIEW_DATE].copy()
    if holdings.empty:
        raise ValueError(f'持股資料中沒有 <= REVIEW_DATE ({REVIEW_DATE.date()}) 的快照')

    dates = sorted(holdings['DATE_'].unique())
    period_start = dates[0]
    period_end = dates[-1]
    print(f'  [REVIEW_DATE] 截至 {REVIEW_DATE.date()}; 實際 period_end = {period_end.date()} ({len(dates)} 個交易日)')

    # ---- 交易 ----
    tr = pd.read_excel(fp_holdings, sheet_name='交易')
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
    # 同樣按 REVIEW_DATE 截斷
    tr = tr[tr['交易日期'] <= REVIEW_DATE].copy()

    # ---- Bloomberg 歸因分析 ----
    bb_sheet_names = pd.ExcelFile(fp_bb).sheet_names
    if 'Bloomberg歸因分析' in bb_sheet_names:
        # 舊版單 sheet 模式
        bb_raw = pd.read_excel(fp_bb, sheet_name='Bloomberg歸因分析', header=None)
    else:
        # 新版: 每 sheet 為一個期間 (sheet 名 YYYYMMDD)
        sheet_to_use = bb_sheet or _pick_bb_sheet(fp_bb, period_end)
        print(f'  [Bloomberg] 使用 sheet: {sheet_to_use} (對應 period_end {period_end.date()})')
        bb_raw = pd.read_excel(fp_bb, sheet_name=sheet_to_use, header=None)
    # 以雙層表頭自動對應欄位，避免欄位新增/重排後固定 slice 失準
    bb_data_row, bb_col_map = _get_bb_column_map(bb_raw)
    bb_cols = [col for col in BB_COLUMN_ORDER if col in bb_col_map]
    bb = bb_raw.iloc[bb_data_row:, [bb_col_map[col] for col in bb_cols]].copy()
    bb.columns = bb_cols
    bb = bb.dropna(subset=['name']).reset_index(drop=True)
    # 將數值欄轉成 float
    for c in bb.columns:
        if c in {'name', 'code'}:
            continue
        bb[c] = pd.to_numeric(bb[c], errors='coerce')
    # 移除時間報酬欄位 (本報告不使用 Timing)
    time_cols = [c for c in ['_time_port', '_time_bench', '_time_active'] if c in bb.columns]
    if time_cols:
        bb = bb.drop(columns=time_cols)

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

    # Active Share = ½ × Σ |w_port - w_bench|
    # 業界慣例使用「期末快照」權重 (point-in-time end-of-period), 反映報告日結構差異
    # 期間平均 (wt_active) 因為前期 cash heavy → 權重被攤平, 不適合作為結構差異指標
    bb_secs_all = bb[~bb['is_sector'] & ~bb['name'].isin(['Holdings', 'Residuals', 'QUANT'])]
    if 'end_wt_active' in bb_secs_all.columns and bb_secs_all['end_wt_active'].notna().any():
        active_share = bb_secs_all['end_wt_active'].abs().sum() / 2 / 100        # 期末 (主要顯示)
    else:
        active_share = bb_secs_all['wt_active'].abs().sum() / 2 / 100
    active_share_avg = bb_secs_all['wt_active'].abs().sum() / 2 / 100             # 期間平均 (reference)

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
        'active_share': active_share,           # 期末口徑 (主要)
        'active_share_avg': active_share_avg,   # 期間平均 (reference)
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
    print(f"  Active Share (期末口徑)      : {pct(t['active_share'], 2)}    (½ × Σ|w_P − w_B|, end-of-period)")
    print(f"  Active Share (期間平均, ref) : {pct(t.get('active_share_avg'), 2)}")
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
        'active_share_avg': t.get('active_share_avg'),
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

    # ---- P&L 結構 (YTD from Reset_*) ----
    sub('P&L 結構分解 (YTD, Reset_* 欄位)')
    total_urcg = h_end['TOTAL_URCG'].sum()       # 已是 Reset_TOTAL_URCG (load_data 覆寫)
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

    # 全部曾持有 ticker 之 YTD P&L (h_end['TOTAL_PL'] 已是 Reset_TOTAL_PL = YTD)
    all_contributors = h_end[h_end['TOTAL_PL'].notna() & (h_end['TOTAL_PL'].abs() > 1.0)][
        ['ticker', 'STK_NAME', 'TOTAL_URCG', 'TOTAL_REALIZED', 'TOTAL_DVD', 'TOTAL_PL']
    ].copy().sort_values('TOTAL_PL', ascending=False).reset_index(drop=True)

    return {
        'held': held, 'held_sorted': held_sorted,
        'top10': top10, 'bot10': bot10,
        'all_contributors': all_contributors,
        'quota_usd': QUOTA_USD,
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
    # 期末權重: 覆寫 wt_* → end_wt_* (本期報表口徑統一用期末快照)
    end_map = {'end_wt_port': 'wt_port', 'end_wt_bench': 'wt_bench', 'end_wt_active': 'wt_active'}
    for src, dst in end_map.items():
        if src in bb.columns and bb[src].notna().any():
            bb[f'_avg_{dst}'] = bb[dst]   # 備份期間平均
            bb[dst] = bb[src]
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
    print("  [註] wP/wB = 期末權重 (end_wt_*, Bloomberg 報告日 snapshot)")
    print("        rP/rB = 期間總報酬, rActive = rP - rB")
    return bb  # 回傳完整 bb (期末權重覆寫已套用), 給 HTML 使用


# =============================================================
# 6. Brinson 歸因 (自算; 多期 + Carino linking)
# =============================================================
def _load_bb_snapshot(fp_bb, sheet_name):
    """讀單一 Bloomberg sheet, 取出 Holdings 列 + 各 sector 列"""
    bb_raw = pd.read_excel(fp_bb, sheet_name=sheet_name, header=None)
    bb_data_row, bb_col_map = _get_bb_column_map(bb_raw)
    bb_cols = [c for c in BB_COLUMN_ORDER if c in bb_col_map]
    bb = bb_raw.iloc[bb_data_row:, [bb_col_map[c] for c in bb_cols]].copy()
    bb.columns = bb_cols
    bb = bb.dropna(subset=['name']).reset_index(drop=True)
    for c in bb.columns:
        if c not in {'name', 'code'}:
            bb[c] = pd.to_numeric(bb[c], errors='coerce')
    holdings_row = bb[bb['name'] == 'Holdings']
    holdings = holdings_row.iloc[0] if len(holdings_row) else None
    sectors = bb[bb['name'].isin(SECTORS_GICS)].copy()
    return holdings, sectors


def _load_all_bb_snapshots(fp_bb):
    """讀 Bloomberg歸因分析.xlsx 全部 sheet, sheet 名為日期 → 各 sheet 為 cumulative attribution"""
    xl = pd.ExcelFile(fp_bb)
    snapshots = []
    for name in xl.sheet_names:
        try:
            date = pd.Timestamp(str(name))
        except Exception:
            continue
        h, secs = _load_bb_snapshot(fp_bb, name)
        if h is None:
            continue
        snapshots.append({'date': date, 'sheet': name, 'holdings': h, 'sectors': secs})
    snapshots.sort(key=lambda x: x['date'])
    return snapshots


def _compute_subperiod_port(holdings_daily, trades, d_start, d_end, ticker_to_sector):
    """
    子期間 sector w_p / r_p — 用每日 TWRR + 時間加權權重

      w_p,i = avg(MV_sector_t / MV_total_t) over days   # 時間加權平均權重
      r_p,i = ∏(1 + daily_sector_return_t) - 1
        其中 daily_sector_return_t = (MV_sector_t - MV_sector_{t-1} - sector_CF_t) / MV_sector_{t-1}
        sector_CF_t = 該日 sector 內所有 ticker 的 net 買進金額 (買 = +, 賣 = -)
    """
    mask = (holdings_daily['DATE_'] >= d_start) & (holdings_daily['DATE_'] <= d_end)
    h_sub = holdings_daily[mask].copy()
    if h_sub.empty or h_sub['DATE_'].nunique() < 2:
        return None
    h_sub['sector'] = h_sub['ticker'].map(ticker_to_sector).fillna('其他')

    # 每日 sector MV 矩陣 (DATE_ × sector)
    sector_mv = h_sub.pivot_table(index='DATE_', columns='sector', values='TOTAL_MV',
                                   aggfunc='sum', fill_value=0).sort_index()

    # 每日 sector CF (signed_amount: 買 > 0, 賣 < 0)
    tr_mask = (trades['交易日期'] >= d_start) & (trades['交易日期'] <= d_end)
    tr_sub = trades[tr_mask].copy()
    if len(tr_sub):
        tr_sub['sector'] = tr_sub['ticker'].map(ticker_to_sector).fillna('其他')
        sector_cf = tr_sub.pivot_table(index='交易日期', columns='sector', values='signed_amount',
                                        aggfunc='sum', fill_value=0)
        # align dates to sector_mv index, columns to all sectors
        sector_cf = sector_cf.reindex(sector_mv.index, fill_value=0)
        for c in sector_mv.columns:
            if c not in sector_cf.columns:
                sector_cf[c] = 0
        sector_cf = sector_cf[sector_mv.columns]
    else:
        sector_cf = pd.DataFrame(0, index=sector_mv.index, columns=sector_mv.columns)

    # 每日 sector return: r_t = (MV_t - MV_{t-1} - CF_t) / MV_{t-1}
    mv_prev = sector_mv.shift(1)
    cf_today = sector_cf
    # 計算 daily returns; 防呆: 當 MV_{t-1} <= 0 時, daily return = 0 (新部位開始, 視為 day-1 無報酬)
    daily_r = (sector_mv - mv_prev - cf_today) / mv_prev.where(mv_prev > 0)
    daily_r = daily_r.fillna(0)
    # 第一天 NaN → 0 (沒有前一日)
    daily_r.iloc[0] = 0

    # 各 sector TWRR: ∏(1 + r_t) - 1
    twrr = (1 + daily_r).prod(axis=0) - 1

    # 時間加權平均權重 w_p,i (用 sub-period 每日權重平均)
    total_mv_daily = sector_mv.sum(axis=1)
    sector_w_daily = sector_mv.div(total_mv_daily.replace(0, np.nan), axis=0).fillna(0)
    avg_weight = sector_w_daily.mean()  # average over days

    # 組成輸出
    rows = []
    for sec in sector_mv.columns:
        rows.append({
            'sector': sec,
            'w_p': float(avg_weight.get(sec, 0)),
            'r_p': float(twrr.get(sec, 0)),
            'avg_mv': float(sector_mv[sec].mean()),
            'capital': float(sector_mv[sec].max()),
            'delta_pl': float((sector_mv[sec].iloc[-1] - sector_mv[sec].iloc[0]) - sector_cf[sec].sum()),
        })
    return pd.DataFrame(rows)


def compute_brinson_multiperiod(d, fp_bb):
    """
    多期 Brinson + Carino linking. 子期間定義為相鄰 Bloomberg snapshot 之間.

      每子期間 Brinson:
        Allocation_i(t) = (w_P,i(t) - w_B,i(t)) × (r_B,i(t) - r_B_total(t))
        Selection_i(t)  = w_P,i(t) × (r_P,i(t) - r_B,i(t))

      Carino linking:
        R_p = ∏(1 + r_P_implied,t) - 1
        R_b = ∏(1 + r_B_total,t) - 1
        R_active = R_p - R_b
        k = ln(1+R_p)/R_p − ln(1+R_b)/R_b 之比 (或更精確: ln(R_active+1)/R_active)
        k_t = (ln(1+r_p,t) - ln(1+r_b,t)) / (r_p,t - r_b,t)
        Total_alloc = Σ (k_t / k) × alloc_total(t)
    """
    snapshots = _load_all_bb_snapshots(fp_bb)
    # 按 REVIEW_DATE 截斷
    snapshots = [s for s in snapshots if s['date'] <= REVIEW_DATE]
    if len(snapshots) < 2:
        # 只有 1 個 snapshot, 退回單期計算
        return None, None

    # ticker → sector 從最後一個 snapshot 取
    last_snap = snapshots[-1]
    bb_secs_last = pd.read_excel(fp_bb, sheet_name=last_snap['sheet'], header=None)
    bb_data_row, bb_col_map = _get_bb_column_map(bb_secs_last)
    bb_cols = [c for c in BB_COLUMN_ORDER if c in bb_col_map]
    bb_full = bb_secs_last.iloc[bb_data_row:, [bb_col_map[c] for c in bb_cols]].copy()
    bb_full.columns = bb_cols
    bb_full = bb_full.dropna(subset=['name']).reset_index(drop=True)
    bb_full['is_sector'] = bb_full['name'].isin(SECTORS_GICS)
    bb_full['sector'] = np.where(bb_full['is_sector'], bb_full['name'], np.nan)
    bb_full['sector'] = bb_full['sector'].ffill()
    sec_universe = bb_full[~bb_full['is_sector'] & ~bb_full['name'].isin(['Holdings', 'Residuals', 'QUANT'])]
    sec_universe = sec_universe[sec_universe['sector'].notna()]
    if 'code' in sec_universe.columns:
        sec_universe = sec_universe.copy()
        sec_universe['_code'] = sec_universe['code'].apply(norm_ticker)
        for c in [col for col in sec_universe.columns if col not in {'name', 'code', 'sector', '_code', 'is_sector'}]:
            sec_universe[c] = pd.to_numeric(sec_universe[c], errors='coerce')
        sec_sorted = sec_universe[sec_universe['_code'].notna()].sort_values('wt_port', ascending=False, na_position='last')
        ticker_to_sector = {}
        for _, r in sec_sorted.iterrows():
            c = r['_code']
            if c and c not in ticker_to_sector:
                ticker_to_sector[c] = r['sector']
    else:
        ticker_to_sector = {}

    # 將 cumulative snapshot 轉成子期間 (cumulative → period return via ratio)
    sub_periods = []
    for i, snap in enumerate(snapshots):
        # 累積至 snap['date'] 的 Bloomberg holdings
        h_cum = snap['holdings']
        secs_cum = snap['sectors'].copy()
        # cum_r_b_total
        cum_r_b_total = float(h_cum['tr_bench']) / 100 if 'tr_bench' in h_cum.index else 0
        cum_sectors = {}
        for _, row in secs_cum.iterrows():
            cum_sectors[row['name']] = {
                'w_b': float(row['wt_bench']) / 100 if pd.notna(row['wt_bench']) else 0,
                'r_b': float(row['tr_bench']) / 100 if pd.notna(row['tr_bench']) else 0,
            }
        snap['_cum_r_b_total'] = cum_r_b_total
        snap['_cum_sectors'] = cum_sectors

    # 期初 = 持股最早日期 (2025-12-31) 或 1/1
    period_start = d['totals']['period_start']
    prev_date = period_start
    prev_cum_r_b_total = 0
    prev_cum_sectors = {sec: {'w_b': 0, 'r_b': 0} for sec in SECTORS_GICS}

    for snap in snapshots:
        # 子期間
        r_b_total = (1 + snap['_cum_r_b_total']) / (1 + prev_cum_r_b_total) - 1 if (1 + prev_cum_r_b_total) > 0 else snap['_cum_r_b_total']
        sectors_sub = {}
        for sec, cum in snap['_cum_sectors'].items():
            prev = prev_cum_sectors.get(sec, {'w_b': 0, 'r_b': 0})
            sub_r_b = (1 + cum['r_b']) / (1 + prev['r_b']) - 1 if (1 + prev['r_b']) > 0 else cum['r_b']
            # 用「期末權重」當該子期間的 w_b (簡化, 真正應用期間平均)
            sectors_sub[sec] = {'w_b': cum['w_b'], 'r_b': sub_r_b}
        # 組合側
        port_sec = _compute_subperiod_port(d['holdings'], d['trades'], prev_date, snap['date'], ticker_to_sector)
        if port_sec is None:
            prev_date = snap['date']
            prev_cum_r_b_total = snap['_cum_r_b_total']
            prev_cum_sectors = snap['_cum_sectors']
            continue
        sub_periods.append({
            'date_start': prev_date,
            'date_end': snap['date'],
            'r_b_total': r_b_total,
            'sectors_bench': sectors_sub,
            'port_sec': port_sec,
        })
        prev_date = snap['date']
        prev_cum_r_b_total = snap['_cum_r_b_total']
        prev_cum_sectors = snap['_cum_sectors']

    # 計算每子期間的 Brinson — 3 components 分開: Allocation / Selection_classic / Interaction
    all_sectors = set()
    for sp in sub_periods:
        all_sectors.update(sp['sectors_bench'].keys())
        all_sectors.update(sp['port_sec']['sector'].tolist())
    all_sectors = sorted(all_sectors)

    sub_alloc_total_list = []
    sub_select_total_list = []
    sub_interact_total_list = []
    sub_r_p_implied_list = []
    sub_r_b_total_list = []
    sector_effects_raw = {sec: {'allocation': 0, 'selection': 0, 'interaction': 0} for sec in all_sectors}

    for sp in sub_periods:
        port_dict = sp['port_sec'].set_index('sector').to_dict('index')
        alloc_t = 0
        select_t = 0
        interact_t = 0
        r_p_implied_t = 0
        for sec in all_sectors:
            w_p = port_dict.get(sec, {}).get('w_p', 0)
            r_p = port_dict.get(sec, {}).get('r_p', 0)
            if pd.isna(r_p):
                r_p = 0
            w_b = sp['sectors_bench'].get(sec, {}).get('w_b', 0)
            r_b = sp['sectors_bench'].get(sec, {}).get('r_b', 0)
            alloc = (w_p - w_b) * (r_b - sp['r_b_total'])
            select = w_b * (r_p - r_b)        # classic Selection (用 wB)
            interact = (w_p - w_b) * (r_p - r_b)
            sector_effects_raw[sec]['allocation'] += alloc
            sector_effects_raw[sec]['selection'] += select
            sector_effects_raw[sec]['interaction'] += interact
            alloc_t += alloc
            select_t += select
            interact_t += interact
            r_p_implied_t += w_p * r_p
        sub_alloc_total_list.append(alloc_t)
        sub_select_total_list.append(select_t)
        sub_interact_total_list.append(interact_t)
        sub_r_p_implied_list.append(r_p_implied_t)
        sub_r_b_total_list.append(sp['r_b_total'])

    # Geometric (compound) totals
    R_p = np.prod([1 + r for r in sub_r_p_implied_list]) - 1
    R_b = np.prod([1 + r for r in sub_r_b_total_list]) - 1
    R_active = R_p - R_b

    # Carino smoothing
    def safe_log_ratio(r):
        return np.log(1 + r) if (1 + r) > 0 else 0

    if abs(R_active) > 1e-12 and (1 + R_p) > 0 and (1 + R_b) > 0:
        k = (safe_log_ratio(R_p) - safe_log_ratio(R_b)) / R_active
    else:
        k = 1.0

    # 各子期間 smoothing factor
    smoothing = []
    for r_p, r_b in zip(sub_r_p_implied_list, sub_r_b_total_list):
        r_a = r_p - r_b
        if abs(r_a) < 1e-12 or (1 + r_p) <= 0 or (1 + r_b) <= 0:
            smoothing.append(1.0)
        else:
            k_t = (safe_log_ratio(r_p) - safe_log_ratio(r_b)) / r_a
            smoothing.append(k_t / k if k != 0 else 1.0)

    # 套 smoothing (3 self-computed components) — Carino 保證 Σ = R_p − R_b (self)
    total_alloc = sum(a * s for a, s in zip(sub_alloc_total_list, smoothing))
    total_select = sum(s * sm for s, sm in zip(sub_select_total_list, smoothing))
    total_interact = sum(i * sm for i, sm in zip(sub_interact_total_list, smoothing))

    # 殘差 = 自算 Active − Σ 3 項 (Carino 後應接近 0, 僅浮點誤差)
    residual = R_active - (total_alloc + total_select + total_interact)

    # Per-sector 套 smoothing (用每子期間的 smoothing factor)
    sector_effects_smoothed = {sec: {'allocation': 0, 'selection': 0, 'interaction': 0}
                               for sec in all_sectors}
    for i, sp in enumerate(sub_periods):
        port_dict = sp['port_sec'].set_index('sector').to_dict('index')
        sm = smoothing[i]
        for sec in all_sectors:
            w_p = port_dict.get(sec, {}).get('w_p', 0)
            r_p = port_dict.get(sec, {}).get('r_p', 0)
            if pd.isna(r_p):
                r_p = 0
            w_b = sp['sectors_bench'].get(sec, {}).get('w_b', 0)
            r_b = sp['sectors_bench'].get(sec, {}).get('r_b', 0)
            sector_effects_smoothed[sec]['allocation'] += (w_p - w_b) * (r_b - sp['r_b_total']) * sm
            sector_effects_smoothed[sec]['selection'] += w_b * (r_p - r_b) * sm
            sector_effects_smoothed[sec]['interaction'] += (w_p - w_b) * (r_p - r_b) * sm

    # 組成輸出 DataFrame
    rows = []
    last_port_dict = sub_periods[-1]['port_sec'].set_index('sector').to_dict('index') if sub_periods else {}
    last_bench = sub_periods[-1]['sectors_bench'] if sub_periods else {}
    # 各 sector 整期 r_p / r_b 也用 Carino-smoothed sub-period 累加 (與 component 一致)
    for sec in all_sectors:
        # sector 整期 r_p (跨子期間 sub-period 報酬 × smoothing 之和)
        sec_rp = sum(
            (sp['port_sec'].set_index('sector').to_dict('index').get(sec, {}).get('r_p', 0) or 0) * sm
            for sp, sm in zip(sub_periods, smoothing)
        )
        sec_rb = sum(
            sp['sectors_bench'].get(sec, {}).get('r_b', 0) * sm
            for sp, sm in zip(sub_periods, smoothing)
        )
        rows.append({
            'sector': sec,
            'w_p': last_port_dict.get(sec, {}).get('w_p', 0),
            'w_b': last_bench.get(sec, {}).get('w_b', 0),
            'r_p': sec_rp,
            'r_b': sec_rb,
            'allocation': sector_effects_smoothed[sec]['allocation'],
            'selection': sector_effects_smoothed[sec]['selection'],
            'interaction': sector_effects_smoothed[sec]['interaction'],
        })
    df = pd.DataFrame(rows)
    df['w_active'] = df['w_p'] - df['w_b']
    df['r_active'] = df['r_p'] - df['r_b']
    df['total'] = df['allocation'] + df['selection'] + df['interaction']
    df = df[df['sector'] != '其他'].copy()
    df = df.sort_values('total', ascending=False).reset_index(drop=True)

    summary = {
        'allocation_total': total_alloc,
        'selection_total': total_select,
        'interaction_total': total_interact,
        'r_p_compounded': R_p,
        'r_b_compounded': R_b,
        'active': R_active,
        'residual': residual,
        'sub_periods': sub_periods,
        'smoothing': smoothing,
        'sub_alloc_total': sub_alloc_total_list,
        'sub_select_total': sub_select_total_list,
        'sub_interact_total': sub_interact_total_list,
        'sub_r_p_implied': sub_r_p_implied_list,
        'sub_r_b_total': sub_r_b_total_list,
    }
    return df, summary


def compute_brinson_self(d):
    """
    Brinson 2-component (Allocation + Selection 含 Interaction)

      Allocation_i = (w_P,i - w_B,i) × (r_B,i - r_B_total)
      Selection_i  = w_P,i × (r_P,i - r_B,i)
      Σ = r_P_implied - r_B_total = Active Return

    口徑:
      - 組合: 用 期末 持股(庫存) 的 TOTAL_MV (權重) 與 TOTAL_PL / TOTAL_COST (報酬)
      - 基準: 用 Bloomberg sectors 的 wt_bench, tr_bench (期間 cap-weighted)
      - 個股 → sector mapping: 由 Bloomberg securities 的 (code → sector) 取得
    """
    h_end = d['h_end'].copy()
    bb_secs = d['bb_securities'].copy()
    bb_sectors = d['bb_sectors'].copy()
    bb_holdings = d['bb_holdings']

    # ticker → sector mapping
    if 'code' in bb_secs.columns:
        bb_secs['_code'] = bb_secs['code'].apply(norm_ticker)
        # 有 wt_port>0 的優先 (避免 share-class 重複歧義)
        bb_sorted = bb_secs[bb_secs['_code'].notna()].sort_values(
            'wt_port', ascending=False, na_position='last'
        )
        ticker_to_sector = {}
        for _, r in bb_sorted.iterrows():
            c = r['_code']
            if c and c not in ticker_to_sector:
                ticker_to_sector[c] = r['sector']
    else:
        ticker_to_sector = {}

    h_end['sector'] = h_end['ticker'].map(ticker_to_sector).fillna('其他')

    # 期內 max cost per ticker (峰值資本): 解決「期末 cost=0 但 pnl≠0 (已平倉)」的分母扭曲
    ticker_max_cost = d['holdings'].groupby('ticker')['TOTAL_COST'].max()
    h_end['_max_cost'] = h_end['ticker'].map(ticker_max_cost).fillna(0)

    # 聚合 per-sector (組合側)
    port = h_end.groupby('sector').agg(
        mv=('TOTAL_MV', 'sum'),
        cost_end=('TOTAL_COST', 'sum'),       # 期末 cost (僅現存部位)
        capital=('_max_cost', 'sum'),         # 期內 max cost 加總 (峰值資本)
        pnl=('TOTAL_PL', 'sum'),              # 含已平倉 P&L
        n=('ticker', 'count'),
    ).reset_index()

    total_mv = h_end['TOTAL_MV'].sum()
    port['w_p'] = port['mv'] / total_mv if total_mv else 0
    port['r_p'] = port['pnl'] / port['capital'].replace(0, np.nan)

    # 基準 per-sector (Bloomberg)
    bench = bb_sectors[['name', 'wt_bench', 'tr_bench']].copy()
    bench.columns = ['sector', 'w_b_pct', 'r_b_pct']
    bench['w_b'] = bench['w_b_pct'] / 100  # Bloomberg 值為 %
    bench['r_b'] = bench['r_b_pct'] / 100

    df = port.merge(bench[['sector', 'w_b', 'r_b']], on='sector', how='outer')
    df['w_p'] = df['w_p'].fillna(0)
    df['w_b'] = df['w_b'].fillna(0)
    df['cost_end'] = df['cost_end'].fillna(0)
    df['capital'] = df['capital'].fillna(0)
    df['mv'] = df['mv'].fillna(0)
    df['pnl'] = df['pnl'].fillna(0)
    df['n'] = df['n'].fillna(0).astype(int)

    # r_B_total 取自 Bloomberg Holdings 列
    r_b_total = float(bb_holdings['tr_bench']) / 100 if bb_holdings is not None else 0.0

    # Brinson
    df['r_p_clean'] = df['r_p'].fillna(0)
    df['r_b_clean'] = df['r_b'].fillna(0)
    df['w_active'] = df['w_p'] - df['w_b']
    df['r_active'] = df['r_p_clean'] - df['r_b_clean']
    df['allocation'] = df['w_active'] * (df['r_b_clean'] - r_b_total)
    df['selection'] = df['w_p'] * (df['r_p_clean'] - df['r_b_clean'])
    df['total'] = df['allocation'] + df['selection']

    df = df.sort_values('total', ascending=False).reset_index(drop=True)

    summary = {
        'allocation_total': df['allocation'].sum(),
        'selection_total': df['selection'].sum(),
        'r_p_implied': (df['w_p'] * df['r_p_clean']).sum(),
        'r_b_total': r_b_total,
    }
    summary['active'] = summary['r_p_implied'] - r_b_total

    return df, summary, ticker_to_sector


def section_attribution(d):
    hr('5. Brinson 歸因 (自算; 多期 Carino 累乘)')

    df, summary = compute_brinson_multiperiod(d, FP_BB)
    if df is None:
        # 退回單期計算
        df, summary, _ = compute_brinson_self(d)
        print("  [警告] Bloomberg 多 sheet 載入失敗, 退回單期計算")

    t = d['totals']

    n_sub = len(summary.get('sub_periods', []))
    print(f"  子期間數: {n_sub} (相鄰 Bloomberg snapshot 之間)")
    if n_sub > 0:
        for i, sp in enumerate(summary['sub_periods'], 1):
            r_p = summary['sub_r_p_implied'][i-1]
            r_b = summary['sub_r_b_total'][i-1]
            sm = summary['smoothing'][i-1]
            print(f"    P{i}  {sp['date_start'].date()} → {sp['date_end'].date()}:"
                  f"  r_P={r_p*100:+7.3f}%  r_B={r_b*100:+7.3f}%  Carino k_t/k={sm:.4f}")
    print()
    print(f"  [口徑] 組合: 持股 daily snapshot, 子期間用 (avg MV / 子期間 max cost) 算 w_p, r_p")
    print(f"         基準: Bloomberg 各 sheet (cumulative) → 子期間 r_B = (1+cum_t)/(1+cum_{{t-1}}) - 1")
    print(f"         複合: Carino smoothing — sub effects × (k_t/k) 累加, 與 R_active 完美 reconcile")
    print()
    print()
    alloc = summary.get('allocation_total', 0)
    sel = summary.get('selection_total', 0)
    interact = summary.get('interaction_total', 0)
    sum_3 = alloc + sel + interact
    r_p_compounded = summary.get('r_p_compounded', 0)
    r_b_compounded = summary.get('r_b_compounded', 0)
    self_active = summary.get('active', 0)
    residual = summary.get('residual', self_active - sum_3)

    print(f"  ★ Active Return 3 項拆解 (純自算, Carino linking):")
    print(f"     Allocation  (Carino smoothed) : {pct(alloc)}")
    print(f"     Selection   (Carino, w_B-based) : {pct(sel)}")
    print(f"     Interaction (Carino)          : {pct(interact)}")
    print(f"     ─────────────────────────────")
    print(f"     Σ 3 項                        : {pct(sum_3)}")
    print(f"     Residual (Carino 浮點誤差)    : {pct(residual)}")
    print(f"     ─────────────────────────────")
    print(f"     R_P − R_B (Self Active)       : {pct(self_active)}    ✓")
    print()
    print(f"  [Reference, 不參與歸因]")
    print(f"  R_P (self compound, ∏(1+r_p,t)−1)        : {pct(r_p_compounded)}")
    print(f"  R_B (self compound, ∏(1+r_b,t)−1)        : {pct(r_b_compounded)}")
    print(f"  Bloomberg TWRR (Portfolio)               : {pct(t['bb_port_return'])}")
    print(f"  Bloomberg TWRR (Benchmark)               : {pct(t['bb_bench_return'])}")
    print()

    sub('Per-sector breakdown')
    # Total 重算為 3 項 (移除 Timing)
    df['total'] = df['allocation'] + df['selection'] + df['interaction']
    cols_to_show = ['sector', 'w_p', 'w_b', 'w_active', 'allocation', 'selection', 'interaction', 'total']
    out = df[cols_to_show].copy()
    fmt_w = lambda x: f'{x*100:+7.2f}'
    fmt_brinson = lambda x: f'{x*100:+7.3f}' if pd.notna(x) and x != 0 else '   0.000'
    for c in ['w_p', 'w_b', 'w_active']:
        out[c] = out[c].apply(fmt_w)
    for c in ['allocation', 'selection', 'interaction', 'total']:
        out[c] = out[c].apply(fmt_brinson)
    out.columns = ['Sector', 'wP%', 'wB%', 'wActive%',
                   'Allocation', 'Selection', 'Interaction', 'Total']
    print(out.to_string(index=False))

    return df, summary


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
    hr('7. 量化模型 Edge: 期內持有過的全部持股 vs SPY 母體 (Bloomberg, wt_port 期間平均 > 0)')
    bb_secs = d['bb_securities'].copy()
    # 保留原 Bloomberg 期間平均權重 (含中間賣掉的部位 wt_port>0, 但 end_wt_port=0)
    # 不再覆寫為 end_wt_*, 因為要納入「期內曾持有過」的所有部位 (包含 sold during period)

    # SPY 母體 (期末 wt_bench > 0, 用期末 universe 因為 SPY 成分有可能增刪)
    if 'end_wt_bench' in bb_secs.columns and bb_secs['end_wt_bench'].notna().any():
        spy_universe = bb_secs[bb_secs['end_wt_bench'].notna() & (bb_secs['end_wt_bench'] > 0)].copy()
        spy_universe['wt_bench_use'] = spy_universe['end_wt_bench']
    else:
        spy_universe = bb_secs[bb_secs['wt_bench'].notna() & (bb_secs['wt_bench'] > 0)].copy()
        spy_universe['wt_bench_use'] = spy_universe['wt_bench']
    spy_universe = spy_universe.dropna(subset=['tr_bench']).reset_index(drop=True)
    spy_universe['rank_full'] = spy_universe['tr_bench'].rank(pct=True) * 100

    # 篩選: 期間平均 wt_port > 0 (期內曾持有過, 含中間賣掉) 且 在 SPY 內
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

    sub('命中率 & SPY 百分位 (期末持有 ∩ SPY)')
    print(f"  命中率 (tr_port > 0)            : {int(profitable)} / {n_held_in_spy} = {pct(hit_rate, 1)}")
    print(f"  平均 SPY 百分位                 : {mean_pct:.1f}  (50 = 隨機選股)")
    print(f"  落在 SPY 上半 (>50)             : {pct_above_50:.1f}%")
    print(f"  落在 SPY 前 1/4 (>75)           : {pct_top25:.1f}%")
    print()
    print(f"  SPY 母體等權平均 YTD%           : {pct(spy_avg_uw/100 if spy_avg_uw else None)}")
    print(f"  持股 SPY 部分等權平均 YTD%      : {pct(port_avg_uw/100 if port_avg_uw else None)}")
    print(f"  超額 (等權)                     : {pct((port_avg_uw - spy_avg_uw)/100 if port_avg_uw and spy_avg_uw else None)}")

    sub(f'期末持有部位於 SPY 母體的 YTD% 百分位 ({n_held_in_spy} 檔, sorted desc)')
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

    # vs Random 基準: 若隨機從 SPY 母體選 N 檔 (我們持有 n_held_in_spy 檔), 期望命中 = N × n_held_in_spy / total_spy
    n_spy_total = len(spy_universe)
    for N in [10, 20, 50]:
        expected = N * n_held_in_spy / n_spy_total if n_spy_total else 0
        bright[N]['expected_hit'] = expected
        bright[N]['multiplier_hit'] = (bright[N]['held_in_top'] / expected) if expected > 0 else None
        # avoid: 隨機 n_held 檔 → 期望落入 Bottom N 也是 N × n_held / total; avoided = N - expected_hit_bottom
        expected_avoid = N - expected  # 隨機選 n_held 個, Bottom N 不在持股名單中的期望
        bright[N]['expected_avoid'] = expected_avoid
        bright[N]['multiplier_avoid'] = (bright[N]['avoided_bottom'] / expected_avoid) if expected_avoid > 0 else None

    # IC (Information Coefficient): Spearman rank correlation via rank().corr()
    # 跨整個 SPY 母體, 對 (portfolio_weight, stock_return) 計算 rank correlation
    spy_universe_w_port = spy_universe.copy()
    if 'wt_port' in spy_universe_w_port.columns:
        spy_universe_w_port['_wt_port'] = spy_universe_w_port['wt_port'].fillna(0)
    else:
        spy_universe_w_port['_wt_port'] = 0.0
    if len(spy_universe_w_port) > 2:
        rank_w = spy_universe_w_port['_wt_port'].rank()
        rank_r = spy_universe_w_port['tr_bench'].rank()
        ic_spearman = float(rank_w.corr(rank_r))
    else:
        ic_spearman = None
    # held-only IC: 只看持有部位 weight vs return rank
    if n_held_in_spy >= 3:
        rk_w = held_in_spy['wt_port'].rank()
        rk_r = held_in_spy['tr_port'].rank()
        ic_held = float(rk_w.corr(rk_r))
    else:
        ic_held = None

    sub('量化模型亮點: 強漲股捕捉率 / 弱跌股迴避率 (含 vs Random 顯著性)')
    print(f"  {'目標':<28} {'命中':>8} {'隨機期望':>10} {'倍數':>8}")
    for N in [10, 20, 50]:
        m = bright[N]['multiplier_hit']
        m_str = f"{m:.1f}x" if m is not None else 'n/a'
        print(f"  SPY Top {N:<3} 漲幅命中 :    {bright[N]['held_in_top']:>3}/{N:<3}  {bright[N]['expected_hit']:>8.2f}  {m_str:>8}")
    for N in [10, 20, 50]:
        m = bright[N]['multiplier_avoid']
        m_str = f"{m:.1f}x" if m is not None else 'n/a'
        print(f"  SPY Bot {N:<3} 跌幅迴避 :    {bright[N]['avoided_bottom']:>3}/{N:<3}  {bright[N]['expected_avoid']:>8.2f}  {m_str:>8}")
    print()
    print(f"  IC (全 SPY, Spearman wt_port × tr_bench) : {ic_spearman:+.3f}" if ic_spearman is not None else "  IC: n/a")
    print(f"  IC (僅持有 24 檔, Spearman wt × ret)      : {ic_held:+.3f}" if ic_held is not None else "  IC (held): n/a")
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

    # ============================================================
    # 擴展母體 = SPY 母體 + 我方 Off-SPY 期末持有
    # 用此 universe 重新評估「全部持股 (in-SPY + off-SPY)」, 取代 SPY-only 指標
    # ============================================================
    off_spy = bb_secs[
        bb_secs['wt_port'].notna() & (bb_secs['wt_port'] > 0) &
        ((bb_secs['wt_bench'].isna()) | (bb_secs['wt_bench'] == 0))
    ].copy()
    # 建擴展 universe — 用 code (BBG ticker) 當 unique key, 避免 share-class 同名重複 (GOOGL/GOOG 都叫 Alphabet公司)
    spy_part = spy_universe[['code', 'name', 'sector', 'tr_bench']].copy()
    spy_part.columns = ['code', 'name', 'sector', '_tr_ext']
    spy_part['_in_spy'] = True
    off_part = off_spy[['code', 'name', 'sector', 'tr_port']].copy() if len(off_spy) > 0 else pd.DataFrame(columns=['code','name','sector','tr_port'])
    if len(off_part):
        off_part.columns = ['code', 'name', 'sector', '_tr_ext']
        off_part['_in_spy'] = False
    ext_universe = pd.concat([spy_part, off_part], ignore_index=True).dropna(subset=['_tr_ext']).reset_index(drop=True)
    # 以 code 去重 (保險防 SPY 母體本身有重複)
    ext_universe = ext_universe.drop_duplicates(subset=['code']).reset_index(drop=True)
    ext_universe['rank_ext'] = ext_universe['_tr_ext'].rank(pct=True) * 100
    n_ext_universe = len(ext_universe)

    # 全部持股 (in-SPY + off-SPY) — 用 code 去重 + merge
    all_held = bb_secs[bb_secs['wt_port'].notna() & (bb_secs['wt_port'] > 0)].copy()
    all_held = all_held.drop_duplicates(subset=['code']).reset_index(drop=True)
    all_held = all_held.merge(ext_universe[['code', 'rank_ext']], on='code', how='left')
    n_all_held = len(all_held)
    profitable_all = int((all_held['tr_port'] > 0).sum())
    hit_rate_all = profitable_all / n_all_held if n_all_held else 0
    mean_rank_all = all_held['rank_ext'].mean() if n_all_held else None
    pct_top25_all = (all_held['rank_ext'] > 75).sum() / n_all_held * 100 if n_all_held else 0
    pct_above_50_all = (all_held['rank_ext'] > 50).sum() / n_all_held * 100 if n_all_held else 0
    port_avg_uw_all = all_held['tr_port'].mean() if n_all_held else 0
    ext_avg_uw = ext_universe['_tr_ext'].mean() if n_ext_universe else 0

    # 擴展母體 Top/Bottom N 捕捉率 — 用 code 去重比對, 避免同名 share-class 干擾
    bright_ext = {}
    held_codes_all = set(all_held['code'].astype(str).str.strip())
    for N in [10, 20, 50]:
        top_n_codes = set(ext_universe.nlargest(N, '_tr_ext')['code'].astype(str).str.strip())
        bot_n_codes = set(ext_universe.nsmallest(N, '_tr_ext')['code'].astype(str).str.strip())
        expected_e = N * n_all_held / n_ext_universe if n_ext_universe else 0
        bright_ext[N] = {
            'held_in_top': len(top_n_codes & held_codes_all),
            'avoided_bottom': len(bot_n_codes - held_codes_all),
            'expected_hit': expected_e,
            'expected_avoid': N - expected_e,
            'multiplier_hit': (len(top_n_codes & held_codes_all) / expected_e) if expected_e > 0 else None,
            'multiplier_avoid': ((len(bot_n_codes - held_codes_all)) / (N - expected_e)) if (N - expected_e) > 0 else None,
        }

    # 擴展母體 IC (Spearman): 用 code 對應 wt_port, 防止同名 share-class 把未持有的也賦予權重
    held_w_dict = dict(zip(all_held['code'].astype(str).str.strip(), all_held['wt_port'].fillna(0)))
    ext_universe['_wt_port'] = ext_universe['code'].astype(str).str.strip().map(lambda c: held_w_dict.get(c, 0))
    if len(ext_universe) > 2:
        rk_w_ext = ext_universe['_wt_port'].rank()
        rk_r_ext = ext_universe['_tr_ext'].rank()
        ic_ext = float(rk_w_ext.corr(rk_r_ext))
    else:
        ic_ext = None
    if n_all_held >= 3:
        rk_w_all = all_held['wt_port'].rank()
        rk_r_all = all_held['tr_port'].rank()
        ic_all_held = float(rk_w_all.corr(rk_r_all))
    else:
        ic_all_held = None

    sub('擴展母體 (SPY + Off-SPY 持有) 評估 — 全部持股 (in-SPY + off-SPY)')
    print(f"  擴展母體 = SPY ({len(spy_universe)}) + Off-SPY 持有 ({len(off_spy)}) = {n_ext_universe} 檔")
    print(f"  全部持股 (n_all_held)             : {n_all_held} 檔 (in-SPY {n_held_in_spy} + off-SPY {len(off_spy)})")
    print(f"  選股勝率 (tr_port > 0)            : {profitable_all} / {n_all_held} = {hit_rate_all*100:.1f}%")
    print(f"  等權平均 YTD                      : {port_avg_uw_all:+.2f}%  vs 擴展母體等權 {ext_avg_uw:+.2f}%  超額 {port_avg_uw_all - ext_avg_uw:+.2f} pp")
    print(f"  平均擴展母體排名                  : 第 {mean_rank_all:.1f} 百分位 (前 1/4 占 {pct_top25_all:.1f}%)")
    print(f"  Top 20 漲幅 命中                  : {bright_ext[20]['held_in_top']} / 20 ({bright_ext[20]['multiplier_hit']:.1f}x random)" if bright_ext[20]['multiplier_hit'] else 'n/a')
    print(f"  Bottom 20 跌幅 暴露 (持有 N)      : {20 - bright_ext[20]['avoided_bottom']} / 20")
    print(f"  IC (擴展母體, Spearman)           : {ic_ext:+.3f}" if ic_ext is not None else 'n/a')
    print(f"  IC (僅持有 {n_all_held} 檔)               : {ic_all_held:+.3f}" if ic_all_held is not None else 'n/a')
    print()

    # ============================================================
    # 持有期間對照: 用同期間 SPY 報酬作為公平基準
    # ============================================================
    holdings_df = d['holdings']
    period_end = d['totals']['period_end']
    period_start = d['totals']['period_start']
    period_days = (period_end - period_start).days
    spy_full = (d['totals'].get('bb_bench_return') or 0)  # SPY 全期 TWRR
    # SPY 等效 daily 複利率
    if period_days > 0 and (1 + spy_full) > 0:
        spy_daily_compound = (1 + spy_full) ** (1.0/period_days) - 1
    else:
        spy_daily_compound = 0
    # 每 ticker 首次與最後持有日期 (MV>0); sold positions 的 last_day < period_end
    held_dates = holdings_df[holdings_df['TOTAL_MV'] > 0].groupby('ticker')['DATE_'].agg(['min', 'max']).to_dict('index')

    all_held = all_held.copy()
    all_held['_ticker'] = all_held['code'].apply(lambda c: str(c).strip() if pd.notna(c) else None)
    all_held['_first_day'] = all_held['_ticker'].apply(lambda t: held_dates.get(t, {}).get('min') if t else None)
    all_held['_last_day'] = all_held['_ticker'].apply(lambda t: held_dates.get(t, {}).get('max') if t else None)
    all_held['_days_held'] = all_held.apply(
        lambda r: (r['_last_day'] - r['_first_day']).days if pd.notna(r['_first_day']) and pd.notna(r['_last_day']) else period_days,
        axis=1
    )
    # 標註是否仍在帳 (期末持有) — 用 days_held 判斷較不可靠, 直接看 end_wt_port
    all_held['_still_held'] = all_held.get('end_wt_port', pd.Series([0]*len(all_held))).fillna(0) > 0
    # SPY 同期間複利報酬 (從 first_day 到 last_day)
    all_held['_spy_window_ret'] = all_held['_days_held'].apply(
        lambda d: ((1 + spy_daily_compound) ** d - 1) * 100 if d > 0 else 0
    )
    all_held['_window_excess'] = all_held['tr_port'] - all_held['_spy_window_ret']

    # 持有時點旗標: 期初 / 期中 / 期末 (各檢查當天 TOTAL_MV > 0)
    holdings_dates = sorted(holdings_df['DATE_'].unique())
    mid_date = holdings_dates[len(holdings_dates) // 2] if holdings_dates else None
    held_at_start = set(holdings_df[(holdings_df['DATE_'] == period_start) & (holdings_df['TOTAL_MV'] > 0)]['ticker'])
    held_at_mid = set(holdings_df[(holdings_df['DATE_'] == mid_date) & (holdings_df['TOTAL_MV'] > 0)]['ticker']) if mid_date is not None else set()
    held_at_end = set(holdings_df[(holdings_df['DATE_'] == period_end) & (holdings_df['TOTAL_MV'] > 0)]['ticker'])
    all_held['_at_start'] = all_held['_ticker'].apply(lambda t: t in held_at_start)
    all_held['_at_mid'] = all_held['_ticker'].apply(lambda t: t in held_at_mid)
    all_held['_at_end'] = all_held['_ticker'].apply(lambda t: t in held_at_end)

    n_beat_window = int((all_held['_window_excess'] > 0).sum())
    win_excess_avg = float(all_held['_window_excess'].mean())
    win_excess_weighted = float((all_held['wt_port'] * all_held['_window_excess']).sum() / all_held['wt_port'].sum()) if all_held['wt_port'].sum() else 0

    sub('持有期間對照 (SPY 同窗口報酬作公平基準)')
    print(f"  SPY 全期 TWRR              : {spy_full*100:+.2f}%  ({period_days} 天)")
    print(f"  SPY 等效 daily 複利率      : {spy_daily_compound*100:+.4f}% / 天")
    print(f"  跑贏同窗口 SPY 的檔數      : {n_beat_window} / {n_all_held} = {n_beat_window/n_all_held*100:.1f}%")
    print(f"  平均同窗口超額 (等權)      : {win_excess_avg:+.2f} pp")
    print(f"  平均同窗口超額 (權重加權)  : {win_excess_weighted:+.2f} pp")
    print()
    # 補入 sector 平均 SPY 報酬率作對照
    sector_avg = spy_universe.groupby('sector')['tr_bench'].mean().to_dict()
    if len(off_spy) > 0 and 'sector' in off_spy.columns:
        off_spy['sector_avg_spy'] = off_spy['sector'].map(sector_avg)
        off_spy['excess_vs_sector'] = off_spy['tr_port'] - off_spy['sector_avg_spy']

    sub('Off-SPY 持股評估 (期末持有但不在 SPY 母體)')
    if len(off_spy) > 0:
        n_off = len(off_spy)
        win_off = int((off_spy['tr_port'] > 0).sum())
        avg_off = off_spy['tr_port'].mean()
        beat_sector_n = int((off_spy['excess_vs_sector'] > 0).sum())
        weighted_avg = (off_spy['wt_port'] * off_spy['tr_port']).sum() / off_spy['wt_port'].sum() if off_spy['wt_port'].sum() else 0
        print(f"  Off-SPY 檔數                : {n_off}")
        print(f"  期末權重合計                : {off_spy['wt_port'].sum():.2f}%")
        print(f"  勝率 (tr_port > 0)          : {win_off} / {n_off} = {win_off/n_off*100:.1f}%")
        print(f"  等權平均 YTD%               : {avg_off:+.2f}%")
        print(f"  權重加權平均 YTD%           : {weighted_avg:+.2f}%")
        print(f"  跑贏同產業 SPY 平均的檔數   : {beat_sector_n} / {n_off}")
        print(f"  SPY 母體等權平均 YTD%       : {spy_avg_uw:+.2f}% (參考)")
        print()
        off_disp = off_spy[['name', 'sector', 'wt_port', 'tr_port', 'sector_avg_spy', 'excess_vs_sector']].copy()
        off_disp = off_disp.sort_values('tr_port', ascending=False)
        for c in ['wt_port', 'tr_port', 'sector_avg_spy', 'excess_vs_sector']:
            off_disp[c] = off_disp[c].apply(lambda x: f'{x:+7.2f}' if pd.notna(x) else '   n/a')
        off_disp.columns = ['名稱', 'Sector', '組合權重%', 'YTD%', '同產業SPY平均%', '超額%']
        print(off_disp.to_string(index=False))
        off_spy_stats = {
            'n': n_off,
            'wt_sum': float(off_spy['wt_port'].sum()),
            'win_count': win_off,
            'win_rate': win_off / n_off,
            'avg_uw': float(avg_off),
            'weighted_avg': float(weighted_avg),
            'beat_sector_n': beat_sector_n,
            'detail': off_spy.sort_values('tr_port', ascending=False),
        }
    else:
        print('  (無 Off-SPY 持股)')
        off_spy_stats = None
    print()

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
            'expected_hit': bright[N]['expected_hit'],
            'expected_avoid': bright[N]['expected_avoid'],
            'multiplier_hit': bright[N]['multiplier_hit'],
            'multiplier_avoid': bright[N]['multiplier_avoid'],
        } for N in [10, 20, 50]},
        'n_held_in_spy': n_held_in_spy,
        'n_spy_total': n_spy_total,
        'n_profitable_in_spy': int(profitable),
        'hit_rate': hit_rate,
        'mean_pct': mean_pct,
        'pct_top25': pct_top25,
        'pct_above_50': pct_above_50,
        'port_avg_uw': port_avg_uw,
        'spy_avg_uw': spy_avg_uw,
        'ic_spearman': ic_spearman,
        'ic_held': ic_held,
        'off_spy_stats': off_spy_stats,
        # 擴展母體 (SPY + Off-SPY 持有) 評估指標
        'ext': {
            'n_universe': n_ext_universe,
            'n_off_spy_held': len(off_spy),
            'n_all_held': n_all_held,
            'hit_rate': hit_rate_all,
            'profitable_all': profitable_all,
            'mean_rank': mean_rank_all,
            'pct_top25': pct_top25_all,
            'pct_above_50': pct_above_50_all,
            'port_avg_uw': port_avg_uw_all,
            'ext_avg_uw': ext_avg_uw,
            'excess_uw': port_avg_uw_all - ext_avg_uw,
            'bright': bright_ext,
            'ic_ext': ic_ext,
            'ic_all_held': ic_all_held,
        },
        # 持有窗口公平對照
        'window': {
            'spy_full_return': spy_full * 100,
            'period_days': period_days,
            'spy_daily_compound': spy_daily_compound * 100,
            'n_beat_window': n_beat_window,
            'n_all_held': n_all_held,
            'win_excess_avg': win_excess_avg,
            'win_excess_weighted': win_excess_weighted,
            'detail': all_held[['name', 'sector', '_first_day', '_last_day', '_days_held', 'wt_port', 'tr_port', '_spy_window_ret', '_window_excess', '_at_start', '_at_mid', '_at_end']].copy(),
        },
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
    brinson_df, brinson_summary = section_attribution(d)
    results['attribution'] = brinson_df
    results['brinson_summary'] = brinson_summary
    # Daily TWRR 三種 CF timing 假設 (僅用於說明頁面對比, 不影響主要計算)
    pmv = d['holdings'].groupby('DATE_')['TOTAL_MV'].sum().sort_index()
    pcf = d['trades'].groupby('交易日期')['signed_amount'].sum().reindex(pmv.index, fill_value=0)
    mv_pv = pmv.shift(1)
    def _twrr(denom):
        r = (pmv - mv_pv - pcf) / denom
        r = r.fillna(0); r.iloc[0] = 0
        return float((1 + r).prod() - 1)
    results['perf']['twrr_cf_end']    = _twrr(mv_pv.where(mv_pv > 0))
    results['perf']['twrr_cf_middle'] = _twrr((mv_pv + 0.5 * pcf).where((mv_pv + 0.5 * pcf) > 0))
    results['perf']['twrr_cf_start']  = _twrr((mv_pv + pcf).where((mv_pv + pcf) > 0))

    # 把 perf 全部歸因相關數字換成自算 (HTML 與 console 一致, 不再採用 Bloomberg 計算的歸因/TWRR)
    results['perf']['port_return'] = brinson_summary.get('r_p_compounded', results['perf']['port_return'])
    results['perf']['spy_return']  = brinson_summary.get('r_b_compounded', results['perf']['spy_return'])
    results['perf']['active_return'] = brinson_summary.get('active', results['perf']['active_return'])
    results['perf']['industry_active'] = brinson_summary.get('allocation_total', 0)
    results['perf']['selection_active'] = brinson_summary.get('selection_total', 0)
    results['perf']['interaction_active'] = brinson_summary.get('interaction_total', 0)
    results['perf']['residual_active'] = brinson_summary.get('residual', 0)
    results['perf']['active_return_brinson'] = brinson_summary.get('active', 0)
    # 保留 Bloomberg 的 TWRR 作為 reference (不參與歸因)
    results['perf']['bb_port_return'] = d['totals'].get('bb_port_return')
    results['perf']['bb_bench_return'] = d['totals'].get('bb_bench_return')
    results['perf']['bb_active_return'] = d['totals'].get('bb_active_return')
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
