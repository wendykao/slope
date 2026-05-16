# -*- coding: utf-8 -*-
import warnings
warnings.filterwarnings('ignore')
from datetime import datetime
import pandas as pd
import sqlalchemy
import numpy as np
from my_functions import my_functions
from slope_report import build_backtest_report

my_func = my_functions()

# ══════════════════════════════════════════════════════════════════════════════
# 策略參數
# ══════════════════════════════════════════════════════════════════════════════
stg_Name  = 'slope'
index     = 'SPX Index'
ini_amt   = 1_000_000
stk_num   = 5
stop_loss = -0.25
stg_ID    = '01'
group_    = '1'
fund_unit = index

# ── 執行模式 ──────────────────────────────────────────────────────────────────
# load_para=1：從資料庫讀取既有參數；=0：使用下方手動設定的參數
load_para = 1

# 由策略名稱動態取得對應訊號計算函式
stg_func = getattr(my_func, f'strategy_{stg_Name}_indicator')

# ══════════════════════════════════════════════════════════════════════════════
# 日期設定
# ══════════════════════════════════════════════════════════════════════════════
date_format        = '%Y%m%d'
start_date         = '20190101'
start_trade_date   = '20200101'
end_date_d         = datetime.today().date()
start_trade_date_d = datetime.strptime(start_trade_date, date_format)
end_date           = datetime.strftime(end_date_d, date_format)

# ══════════════════════════════════════════════════════════════════════════════
# SQL 連線與輔助函式
# ══════════════════════════════════════════════════════════════════════════════
engine = sqlalchemy.create_engine(
    r'mssql+pyodbc://cowork:cowork1234@PC09801\SQLEXPRESS/FA'
    r'?driver=ODBC+Driver+17+for+SQL+Server')
conn      = engine.connect()
sp_params = {'index_': index, 'StartDt': start_date, 'EndDt': end_date}


def _load_sp(proc_name):
    """執行 Stored Procedure，回傳 (DataFrame, column_list)。"""
    trans = conn.begin()
    try:
        cur  = my_func.exec_procedure(conn, proc_name, sp_params)
        cols = list(cur.keys())
        df   = pd.DataFrame(cur.fetchall(), columns=cols)
        trans.commit()
        return df, cols
    except Exception:
        trans.rollback()
        raise


# ══════════════════════════════════════════════════════════════════════════════
# 讀取SQL資料庫，資料載入
# ══════════════════════════════════════════════════════════════════════════════
df_idx,       _         = _load_sp('com_Idx_1')           # 指數成份股、產業、權重
df_tec,       _         = _load_sp('com_TecD_1')          # 個股價量資料
df_fin,       cols_fin  = _load_sp('pivot_StkFinQ_1')     # 財務數據（pivot 後）
df_adjfactor, _         = _load_sp('com_Adj_Factor_1')    # 複權因子(除權息)

df_tec       = df_tec.sort_values('date_')
df_adjfactor = df_adjfactor.sort_values('stk_bbg')

# 指數日報酬（以參數化查詢）
df_index = pd.read_sql(
    sqlalchemy.text("""
        SELECT date_, PX_Return, PX_OPEN, PX_LAST FROM TecD
        WHERE stk_bbg = :idx AND date_ BETWEEN :start AND :end
        ORDER BY date_ ASC
    """),
    conn, params={'idx': index, 'start': start_date, 'end': end_date})

# ══════════════════════════════════════════════════════════════════════════════
# 資料前處理，包含合併資料表、日期格式統一、還原除權息等
# ══════════════════════════════════════════════════════════════════════════════

# ── 財務資料 ──────────────────────────────────────────────────────────────────
df_fin = (df_fin[cols_fin[:2] + ['TOTAL_EQUITY']]            # 只保留日期、股票代碼、股東權益
          .rename(columns={'revision_date': 'date'})
          .assign(date=lambda d: pd.to_datetime(d['date'], format=date_format))
          .dropna()
          .drop_duplicates(subset=['date', 'stk_bbg'], keep='last'))  # 同日取最新公告

# ── 複權因子 ──────────────────────────────────────────────────────────────────
# Operator_Type==2 代表除法（需取倒數），否則直接使用
df_adjfactor['Adj_Factor_rev'] = np.where(
    df_adjfactor['Adj_Factor_Operator_Type'] == 2,
    1 / df_adjfactor['Adj_Factor'],
    df_adjfactor['Adj_Factor'])

# ── 行情資料 ──────────────────────────────────────────────────────────────────
# 盤中執行時市場尚未收盤，資料庫最新一筆為前一交易日。
# 若今日資料尚未入庫，將前一交易日價格複製一份，日期改為今日，PX_Return=0，
# 使訊號計算涵蓋今日這個時間點，輸出的是截至前一收盤的最新訊號。
last_date = df_tec.iloc[-1]['date_']
if last_date != end_date:
    df_add_tec              = df_tec.loc[df_tec['date_'] == last_date,
                                         ['date_', 'stk_bbg', 'PX_OPEN', 'PX_HIGH', 'PX_LOW', 'PX_LAST']].copy()
    df_add_tec['date_']     = end_date
    df_add_tec['PX_Return'] = 0
    df_tec = pd.concat([df_tec, df_add_tec], ignore_index=True)

# 合併複權因子（outer join 保留停牌日記錄）
df_tec = (df_tec
          .merge(df_adjfactor, left_on=['stk_bbg', 'date_'],
                 right_on=['stk_bbg', 'Adj_date'], how='outer', indicator=True)
          [['stk_bbg', 'date_', 'PX_OPEN', 'PX_HIGH', 'PX_LOW', 'PX_LAST',
            'PX_VOLUME', 'EQY_SH_OUT', 'PX_Return', 'Adj_Factor', 'Adj_Factor_rev']]
          .assign(Adj_Factor_rev=lambda d: d['Adj_Factor_rev'].fillna(1))
          .sort_values(['stk_bbg', 'date_'])
          .drop_duplicates(subset=['stk_bbg', 'date_'], keep='last')
          .dropna(subset=['PX_LAST']))

# 累積複權因子（各股從舊到新逐日累乘，用於計算股利）
df_tec['Adj_Factor_Cul'] = df_tec.groupby('stk_bbg')['Adj_Factor_rev'].cumprod()
df_tec = df_tec.sort_values(['date_', 'stk_bbg'])

# 反推股利率：日報酬率 - 純股價漲跌，殘差即為股利貢獻
df_temp = df_tec.groupby('stk_bbg').apply(
    lambda x: x['PX_Return'] - (
        (x['PX_LAST'] * x['Adj_Factor_Cul']) /
        (x['PX_LAST'].shift(1) * x['Adj_Factor_Cul'].shift(1)) - 1))
df_tec['DVD'] = df_temp.reset_index(level=0, drop=True).fillna(0)
df_tec['DVD'] = df_tec['DVD'].where(df_tec['DVD'].abs() >= 0.0001, 0)  # 過濾浮點雜訊

# 交易旗標 t：1=正常交易日，0=各股最後交易日（下市/併購後清倉），2=停牌
df_tec['t'] = 1
df_tec.loc[df_tec.groupby('stk_bbg').tail(1).index, 't'] = 0
df_tec.loc[df_tec['date_'] == df_tec['date_'].iloc[-1], 't'] = 1  # 最新日強制設為交易日

df_tec['MCap'] = df_tec['EQY_SH_OUT'] * df_tec['PX_LAST']

# ── 統一日期欄位名稱與格式（date_ → date, dtype=datetime）────────────────────
today_dt = datetime.combine(end_date_d, datetime.min.time())

df_tec.rename(columns={'date_': 'date'}, inplace=True)
df_tec['date'] = pd.to_datetime(df_tec['date'], format=date_format)

df_idx.rename(columns={'date_': 'date'}, inplace=True)
df_idx['date'] = pd.to_datetime(df_idx['date'], format=date_format)
# 盤中執行時成份股資料仍為前一交易日；補入今日列（沿用昨日成份），
# 確保 df_factor 合併後今日這一列存在，訊號得以正確輸出。
if df_idx.iloc[-1, 0] != today_dt:
    df_idx = pd.concat(
        [df_idx, df_idx.loc[df_idx['date'] == df_idx['date'].iloc[-1]].assign(date=today_dt)],
        ignore_index=True)

df_index.rename(columns={'date_': 'date'}, inplace=True)
df_index['date'] = pd.to_datetime(df_index['date'], format=date_format)
# 同上，指數今日尚無收盤報酬，補一筆 PX_Return=0 的佔位列供 trade_dates 使用。
if df_index.iloc[-1, 0] != today_dt:
    df_index = pd.concat(
        [df_index, pd.DataFrame([{'date': today_dt, 'PX_Return': 0}])],
        ignore_index=True)

df_idx['GICS_SECTOR'] = df_idx['GICS_SECTOR'].fillna('99').astype(int).astype(str)
df_idx['weight_']     = df_idx['weight_'] / 100

# ── 建立日期映射表 ────────────────────────────────────────────────────────────
# cldr_dates：成份股出現過的所有日曆日（含非交易日）
cldr_dates = (pd.DataFrame({'date': df_idx['date'].unique()})
              .drop_duplicates('date')
              .set_index('date', drop=False))

# trade_dates：指數有收盤報酬的所有交易日，補入今日確保最新日有記錄
trade_dates = (pd.concat([pd.DataFrame({'date': df_index['date'].unique()}),
                           pd.DataFrame({'date': [today_dt]})], ignore_index=True)
               .drop_duplicates('date')
               .set_index('date', drop=False))

trade_dates_start = trade_dates.loc[trade_dates['date'] >= start_trade_date_d]

# date_map：日曆日 → 對應交易日 & 前一交易日，回測時用前收計算持倉
date_map = cldr_dates.merge(trade_dates, left_index=True, right_index=True,
                             how='left', suffixes=['_cldr', '_trade'])
date_map['date_trade'].ffill(inplace=True)
date_map['date_trade_last'] = date_map['date_trade'].shift(1)
date_map.loc[date_map['date_trade'] == date_map['date_trade_last'], 'date_trade_last'] = np.nan
date_map['date_trade_last'].ffill(inplace=True)

# ── 合併成份股、行情、財務資料 ────────────────────────────────────────────────
df_factor = (pd.merge(df_idx, df_tec, on=['date', 'stk_bbg'], how='outer')
             .assign(DVD=lambda d: d['DVD'].fillna(0),
                     Adj_Factor_rev=lambda d: d['Adj_Factor_rev'].fillna(1),
                     t=lambda d: d['t'].fillna(2))        # 停牌日 t=2
             .sort_values(['date', 'stk_bbg']))

df_quant = pd.merge(df_factor, df_fin, on=['date', 'stk_bbg'], how='left')

# 財務欄位補前值（季報空窗期沿用最新公告數值）
df_quant[['TOTAL_EQUITY']] = df_quant.groupby('stk_bbg')[['TOTAL_EQUITY']].ffill()

# 最後一天暫設 PX_LAST=0 讓 dropna 保留該行，清洗後還原為 NaN 再補前值
last_dt = df_quant.iloc[-1, 0]
df_quant.loc[df_quant['date'] == last_dt, ['PX_LAST', 't']] = [0, 1]
df_quant.dropna(subset=['PX_LAST'], inplace=True)
df_quant.loc[df_quant['date'] == last_dt, 'PX_LAST'] = np.nan
df_quant['PX_LAST']    = df_quant.groupby('stk_bbg')['PX_LAST'].ffill()
df_quant['PX_Return']  = df_quant['PX_Return'].fillna(0)   # 停牌日報酬率補 0

# 計算還原除權息的調整收盤價（adj_PX_LAST），各股獨立從最新日反推
signal_cols = ['date', 'stk_bbg', 'PX_OPEN', 'PX_HIGH', 'PX_LOW', 'PX_LAST',
               'PX_Return', 'PX_VOLUME', 'Adj_Factor_Cul', 'weight_', 't', 'TOTAL_EQUITY']
df_signal = df_quant[signal_cols].copy()
df_signal = df_signal.groupby('stk_bbg', as_index=False).apply(
    lambda x: my_func.calculate_adjusted_prices(x, 'PX_LAST', 'PX_Return'))

# 股東權益為負視為財務異常，排除於選股池外
df_signal.loc[df_signal['TOTAL_EQUITY'] <= 0, 'weight_'] = np.nan

# ══════════════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════════════

# ── 決定策略參數 para = [stk_num, stop_loss, crt_chg, period] ─────────────────
if load_para:
    # 從資料庫讀取最新存檔的策略參數
    sql = sqlalchemy.text("""
        SELECT Para1, Para2, Para3 FROM qt_parameter
        WHERE date_ = (
            SELECT TOP 1 date_ FROM qt_parameter
            WHERE stg_id = :stg_id AND fund_unit = :fund_unit
            GROUP BY date_ ORDER BY date_ DESC
        )
        AND stg_id = :stg_id AND fund_unit = :fund_unit;
    """)
    try:
        paras_sql = pd.read_sql(sql, conn, params={'stg_id': stg_ID, 'fund_unit': fund_unit})
        if paras_sql.empty:
            raise ValueError(f"找不到策略參數 (stg_ID={stg_ID}, fund_unit={fund_unit})")
        row  = paras_sql.iloc[0]
        para = [row['Para1'], stop_loss, row['Para3'], int(row['Para2'])]
    except Exception as e:
        print(f"讀取策略參數時發生錯誤: {e}")
        raise
else:
    # 手動指定參數（測試用）
    para = [5, -0.25, 0.0058, 120]

# ── 計算訊號、執行回測、輸出報表 ──────────────────────────────────────────────
df_signal_all = stg_func(df_signal, df_index[['date', 'PX_Return']], trade_dates_start, para[3])
signal_data   = pd.merge(df_factor, df_signal_all, how='left', on=['date', 'stk_bbg'])

portfolio_1     = pd.DataFrame(columns=['date', 'stk_bbg', 'signal', 'indicator'])
performance_set = [ini_amt, 0.001, 0.001, 0.001, 0.3, 1, ini_amt / para[0]]
# performance_set：[初始資金, 滑價, 手續費, 印花稅(賣), 股利稅, 最小交易單位, 單股資金]

perf_tlt, perf_yr, perf, hold_pf, trade, holdings = my_func.performance(
    signal_data, trade_dates_start, date_map, df_index,
    portfolio_1, performance_set, para)

report_path = build_backtest_report(
    performance=perf, performance_year=perf_yr, performance_tlt=perf_tlt,
    hold_portfolio=hold_pf, trade=trade, holdings=holdings, signal_data=signal_data,
    meta=dict(index=index, stk_num=para[0], ini_amt=ini_amt, stop_loss=stop_loss,
              period=int(para[3]), crt_chg=float(para[2]),
              start_date=start_trade_date, end_date=end_date),
    output_path='slope_backtest_report.html', auto_open=True)
print(f"[報表] 已輸出：{report_path}")

# ── 更新今日訊號至資料庫 ──────────────────────────────────────────────────────
# 來源與報表「最新訊號」tab 相同：signal_data 最後一日、指數成份股（weight_ 非空）、有效斜率
latest_date = signal_data['date'].max()
latest_sig  = (signal_data
               .loc[(signal_data['date'] == latest_date) & signal_data['weight_'].notna(),
                    ['date', 'stk_bbg', 'signal', 'indicator']]
               .dropna(subset=['indicator'])
               .copy())
df_sql    = my_func.prepare_df_for_sql(latest_sig, stg_ID, group_, 'indicator')
today_str = df_sql['date_'].iloc[0]

trans = conn.begin()
try:
    conn.execute(
        sqlalchemy.text("DELETE FROM qt_signal WHERE date_ = :d AND stg_id = :s"),
        {'d': today_str, 's': stg_ID})
    df_sql.to_sql('qt_signal', conn, if_exists='append', index=False)
    trans.commit()
    print(f"[資料庫] 訊號已更新 {len(df_sql)} 筆（{today_str}）")
except Exception as e:
    trans.rollback()
    print(f"[資料庫] 訊號寫入失敗: {e}")
    raise

conn.close()
