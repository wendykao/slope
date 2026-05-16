# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
from sqlalchemy import text


class my_functions:
    def __init__(self):
        pass

    # ── SQL 工具 ──────────────────────────────────────────────────────────────

    def exec_procedure(self, session, proc_name, params):
        """執行 SQL Server Stored Procedure，回傳 cursor 供呼叫端 fetchall。"""
        sql_params = ", ".join([f"@{name}='{value}'" for name, value in params.items()])
        sql_string = f"""
            DECLARE @return_value int;
            EXEC @return_value = [dbo].[{proc_name}] {sql_params};
            SELECT 'Return Value' = @return_value;
        """
        return session.execute(text(sql_string))

    def prepare_df_for_sql(self, df, stg_id, group_id, p_factor_col):
        """將 DataFrame 整理成寫入資料庫的格式：重命名欄位、補充 stg_id / group_。"""
        output_df = df.copy()
        rename_dict = {}
        if 'date' in output_df.columns:
            rename_dict['date'] = 'date_'
        if p_factor_col in output_df.columns:
            rename_dict[p_factor_col] = 'ranking'
        if rename_dict:
            output_df = output_df.rename(columns=rename_dict)
        output_df = output_df.assign(stg_id=stg_id, group_=group_id)
        if 'date_' in output_df.columns:
            output_df['date_'] = output_df['date_'].dt.strftime('%Y%m%d')
        final_columns = ['date_', 'stk_bbg', 'stg_id', 'signal', 'ranking', 'group_']
        existing_cols = [col for col in final_columns if col in output_df.columns]
        return output_df[existing_cols]

    # ── 價格調整 ──────────────────────────────────────────────────────────────

    def calculate_adjusted_prices(self, df, column_c, column_r):
        """從最後一天反推，計算還原除權息後的調整收盤價，新增欄位 adj_<column>。"""
        adj_column = 'adj_' + column_c
        df.sort_index(ascending=False, inplace=True)
        price_col = df[column_c].values
        ret_col = df[column_r].values
        adj_price_col = np.zeros(len(df.index))
        adj_price_col[0] = price_col[0]
        for i in range(1, len(price_col)):
            adj_price_col[i] = round(adj_price_col[i - 1] / (1 + ret_col[i - 1]), 6)
        df[adj_column] = adj_price_col
        df.sort_index(ascending=True, inplace=True)
        return df

    # ── 績效輔助 ──────────────────────────────────────────────────────────────

    def max_dd(self, returns):
        """計算最大回撤（Maximum Drawdown）。"""
        r = returns.add(1).cumprod()
        dd = r.div(r.cummax()).sub(1)
        return dd.min()

    def rwindows(self, a, window):
        """以 stride_tricks 建立滾動視窗 3D 陣列，避免 Python 迴圈提升效能。"""
        if a.ndim == 1:
            a = a.reshape(-1, 1)
        shape = a.shape[0] - window + 1, window, a.shape[-1]
        strides = (a.strides[0],) + a.strides
        return np.squeeze(np.lib.stride_tricks.as_strided(a, shape=shape, strides=strides))

    def calc_slope(self, s: np.ndarray, m: np.ndarray):
        """最小平方法計算斜率，除以期初價格做正規化（使不同價位股票可比較）。"""
        s = s.astype(np.float64)
        m = m.astype(np.float64)
        x = np.vstack((np.ones_like(m), m))
        b = np.linalg.pinv(x.dot(x.T)).dot(x).dot(s)
        return b[1] / s[0, :]

    def rolling_calc_slope(self, s_df, m_df, period):
        """對 s_df 每一欄位進行滾動斜率計算，回傳與 s_df 同形狀的 DataFrame。"""
        result = np.ndarray(shape=s_df.shape, dtype=float)
        l, w = s_df.shape
        ls, ws = s_df.values.strides
        result[0:period, :] = np.nan
        s_arr = np.lib.stride_tricks.as_strided(
            s_df.values, shape=(l - period + 1, period, w), strides=(ls, ls, ws))
        m_arr = self.rwindows(m_df.values, period)
        for row in range(period, l):
            result[row, :] = self.calc_slope(s_arr[row - period, :], m_arr[row - period])
        return pd.DataFrame(data=result, index=s_df.index, columns=s_df.columns)

    # ── 策略訊號 ──────────────────────────────────────────────────────────────

    def strategy_slope_indicator(self, strategy_rank_data, df_index, trade_dates_start, para):
        """
        Slope 策略：計算每檔股票調整後收盤的滾動斜率作為指標。
        para：滾動視窗天數（period）。
        斜率 >= 0 → signal=1（買進）；否則 signal=-1。
        """
        # pivot 成 (日期 × 股票) 矩陣，按指數交易日對齊並補前值
        slpo_y = strategy_rank_data.pivot(index='date', columns='stk_bbg', values='adj_PX_LAST')
        slpo_y = pd.merge(df_index.date, slpo_y, on=['date'], how='left')
        slpo_y.fillna(method='ffill', inplace=True)
        slpo_y.set_index('date', drop=True, inplace=True)

        # 線性趨勢的 x 軸（整數序號）
        slpo_x = pd.Series(np.arange(len(slpo_y.index)), slpo_y.index, name='number')

        # 計算滾動斜率，melt 回 long format 與原資料合併
        slopes = self.rolling_calc_slope(slpo_y, slpo_x, para)
        slopes = pd.melt(slopes.reset_index(), id_vars='date', value_name='indicator')
        strategy_rank_data = pd.merge(
            strategy_rank_data, slopes,
            left_on=['date', 'stk_bbg'], right_on=['date', 'variable'], how='left')

        strategy_rank_data['signal'] = strategy_rank_data['indicator'].apply(
            lambda x: 1 if x >= 0 else -1)
        # 最後一個交易日（t==0）強制清倉；非成份股訊號歸零
        strategy_rank_data.loc[strategy_rank_data['t'] == 0, 'signal'] = 0
        strategy_rank_data.loc[pd.isnull(strategy_rank_data['weight_']), 'signal'] = 0
        strategy_rank_data.sort_values(
            by=['date', 'signal', 'indicator'], ascending=[True, False, False], inplace=True)

        return strategy_rank_data[['date', 'stk_bbg', 'signal', 'indicator']]

    # ── 回測績效引擎 ──────────────────────────────────────────────────────────

    def performance(self, signal_data, trade_dates_start, date_map, df_index, portfolio_1, *args):
        """
        完整回測引擎：逐日模擬持倉、交易與損益計算。
        回傳值：
          performance_tlt  -- 整體統計（年化報酬 / 波動 / MDD 等）
          performance_year -- 分年績效
          performance      -- 每日績效
          hold_portfolio   -- 每日持倉清單
          trade            -- 交易明細
          holdings         -- 最終庫存狀態

        performance_set = [ini_amt, slippage, commission, tax_s, dvd_tax, round_lot, single_amt]
        para = [stk_num, stop_loss, crt_chg, period]
        """
        performance_set = args[0]
        para = args[1]
        signal_data.sort_values(
            by=['date', 'signal', 'indicator'], ascending=[True, False, False], inplace=True)

        hold_portfolio = []
        trade = []
        count_stop_loss = 0
        stock_pool = signal_data['stk_bbg'].unique()

        trade_col_name    = ['p_w', 'c_t_1', 't_shares', 'o_t', 'p_t', 't_amt']
        cor_act_col_name  = ['Adj_Factor_rev', 'DVD', 't']
        holdings_col_name = ['shares', 'px', 'cost', 'px_m', 'mv', 'unreal_pl', 'ret', 'dvd', 'real_pl', 'tlt_pl']
        holdings = pd.DataFrame(
            index=stock_pool,
            columns=holdings_col_name + trade_col_name + cor_act_col_name, data=0)
        holdings.index.name = 'stk_bbg'

        perf_col_name   = ['unreal_pl', 'real_pl', 'dvd', 'cost', 'mv', 'tlt_pl']
        ret_col_name    = ['p_ret', 'b_ret', 'p_ret_cul', 'b_ret_cul']
        ret_col_name_add = ['b_O', 'b_L']
        performance = pd.DataFrame(
            index=trade_dates_start.date,
            columns=perf_col_name + ret_col_name + ret_col_name_add, data=0)

        ret_tlt_col_name = ['ret', 'vol', 'r/v', 'max_ret', 'min_ret', 'turn_over', 'MDD']
        performance_tlt = pd.DataFrame(index=['p', 'b'], columns=ret_tlt_col_name, data=0)
        turnover = 0

        # 初始化：取第一個交易日的前一日收盤作為庫存市價
        t_1 = date_map.loc[date_map['date_trade'] == trade_dates_start.date[0], 'date_trade_last']
        signal_data['PX_LAST_fill'] = signal_data.groupby('stk_bbg')['PX_LAST'].fillna(method='ffill')
        holdings['px_m'] = signal_data.loc[
            signal_data['date'] == t_1[0], ['stk_bbg', 'PX_LAST_fill']].set_index('stk_bbg')
        holdings['px_m'].fillna(0, inplace=True)

        df_stop_loss = pd.DataFrame(index=stock_pool, columns=['stp_loss'], data=0)

        for d, date in enumerate(trade_dates_start.date):
            signal_t = signal_data.loc[signal_data['date'] == date]
            holdings['t'] = signal_t.set_index('stk_bbg')['t']

            # 停損判斷：虧損超過閾值則進入冷卻期（冷卻期內不得買入）
            stop_loss_stock = holdings.loc[(holdings['ret'] < para[1]) & (holdings['t'] == 1)].index
            count_stop_loss += len(stop_loss_stock)
            df_stop_loss.loc[df_stop_loss.index.isin(stop_loss_stock), 'stp_loss'] = para[3]
            stop_loss_stock = df_stop_loss.loc[df_stop_loss['stp_loss'] > 0].index
            signal_t.loc[signal_t['stk_bbg'].isin(stop_loss_stock), 'signal'] = 0
            df_stop_loss.loc[df_stop_loss['stp_loss'] > 0, 'stp_loss'] -= 1

            # 整理當日持倉候選清單（原持股 + 候補池）
            n_1 = 0
            portfolio_hold = portfolio_1['stk_bbg']
            portfolio_hold = pd.merge(
                portfolio_hold, signal_t[['date', 'stk_bbg', 'signal', 'indicator', 't']],
                on=['stk_bbg'], how='left')
            portfolio_hold['signal'].fillna(0, inplace=True)
            portfolio_hold['date'].fillna(date, inplace=True)
            portfolio_hold.sort_values(['signal', 'indicator'], ascending=[False, False], inplace=True)
            portfolio_hold.reset_index(inplace=True, drop=True)
            hold_num = len(portfolio_hold)

            portfolio_others = signal_t.loc[
                ~signal_t['stk_bbg'].isin(portfolio_hold['stk_bbg']),
                ['date', 'stk_bbg', 'signal', 'indicator', 't']]
            portfolio_others.sort_values(['signal', 'indicator'], ascending=[False, False], inplace=True)
            portfolio_others = portfolio_others.reset_index(drop=True)

            # 持股不足時從候補池補入
            if hold_num < para[0]:
                new_hold = portfolio_others.iloc[0:int(para[0] - hold_num)]
                new_hold = new_hold.loc[new_hold['t'] < 2]
                portfolio_hold = pd.concat([portfolio_hold, new_hold], ignore_index=True)
                portfolio_hold.reset_index(drop=True, inplace=True)
                portfolio_others = signal_t.loc[
                    ~signal_t['stk_bbg'].isin(portfolio_hold['stk_bbg']),
                    ['date', 'stk_bbg', 'signal', 'indicator', 't']]
                portfolio_others.sort_values(['signal', 'indicator'], ascending=[False, False], inplace=True)
                portfolio_others = portfolio_others.reset_index(drop=True)

            # 以指標差值（para[2]）決定是否換倉
            if len(portfolio_hold) > 0:
                for i, ind in enumerate(portfolio_others.indicator):
                    for i_h in range(n_1, len(portfolio_hold)):
                        if (portfolio_hold.loc[i_h, 't'] < 2) and (portfolio_others.loc[i, 't'] < 2):
                            if ((ind - portfolio_hold.loc[i_h, 'indicator'] > para[2] or
                                    portfolio_hold.loc[i_h, 'signal'] <= 0) and
                                    portfolio_others.loc[i, 'signal'] > 0):
                                for i_j in range(len(portfolio_hold) - 1, i_h, -1):
                                    portfolio_hold.loc[i_j, :] = portfolio_hold.loc[i_j - 1, :]
                                portfolio_hold.loc[i_h, :] = portfolio_others.loc[i, :]
                                n_1 = i_h + 1
                                break
                    if i_h == len(portfolio_hold) - 1:
                        break

            portfolio_1 = portfolio_hold.loc[portfolio_hold['signal'] > 0]
            if not portfolio_1.empty:
                hold_portfolio.append(portfolio_1)

            # 更新當日市價、股利、除權因子
            px_prev = pd.DataFrame(holdings['px_m'])
            holdings[cor_act_col_name] = 0
            holdings[['o_t', 'px_m', 'vol', 'Adj_Factor_rev', 'DVD', 't']] = signal_t[
                ['stk_bbg', 'PX_OPEN', 'PX_LAST', 'PX_VOLUME', 'Adj_Factor_rev', 'DVD', 't']
            ].set_index('stk_bbg')
            holdings['DVD'].fillna(0, inplace=True)
            holdings['Adj_Factor_rev'].fillna(1, inplace=True)
            holdings['t'].fillna(2, inplace=True)

            # 除權：調整庫存股數與均價
            holdings['shares'] = holdings['shares'] * holdings['Adj_Factor_rev']
            holdings['px'] = holdings['cost'] / holdings['shares']
            # 累計股利（稅後）
            holdings['dvd'] = holdings['dvd'] + holdings['mv'] * holdings['DVD'] * (1 - performance_set[4])

            # 計算權重變動，並執行交易
            holdings['p_w_1'] = holdings['p_w']
            holdings['p_w'] = 0
            holdings.loc[portfolio_1['stk_bbg'], 'p_w'] = 1 / para[0]
            holdings['p_w_1'] = holdings['p_w'] - holdings['p_w_1']

            if abs(holdings['p_w_1']).sum() > 0:
                # 計算交易股數（使用開收盤均價成交）
                holdings['t_shares'] = round(
                    holdings['p_w_1'] * performance_set[0] / px_prev['px_m'] / performance_set[5], 0
                ) * performance_set[5]
                holdings['p_t'] = (holdings['o_t'] + holdings['px_m']) / 2
                holdings['t_shares'].fillna(0, inplace=True)
                holdings['t_shares'] = holdings['t_shares'] * holdings['Adj_Factor_rev']
                holdings.loc[holdings['p_w_1'] < 0, 't_shares'] = -holdings['shares']  # 全數賣出

                # 計算含手續費 / 稅的交易金額
                holdings['t_amt'] = holdings['t_shares'] * holdings['p_t']
                holdings.loc[holdings['t_shares'] > 0, 't_amt'] *= (1 + performance_set[1] + performance_set[2])
                holdings.loc[holdings['t_shares'] < 0, 't_amt'] *= (
                    1 - performance_set[1] - performance_set[2] - performance_set[3])

                turnover += abs(holdings['t_amt']).sum()
                df_trade = holdings.loc[holdings['t_shares'] != 0, ['t_shares', 't_amt']].copy()
                df_trade['date'] = date
                df_trade.reset_index(inplace=True)
                df_trade.set_index('date', inplace=True, drop=True)
                if not df_trade.empty:
                    trade.append(df_trade)

                # 更新庫存股數、成本、已實現損益
                holdings['shares'] += holdings['t_shares']
                holdings.loc[holdings['t_shares'] > 0, 'cost'] += holdings['t_amt']
                holdings.loc[holdings['t_shares'] < 0, 'cost'] += holdings['t_shares'] * holdings['px']
                holdings.loc[holdings['shares'] == 0, 'cost'] = 0
                holdings.loc[holdings['t_shares'] < 0, 'real_pl'] += (
                    -holdings['t_amt'] + holdings['t_shares'] * holdings['px'])
                holdings['px'] = holdings['cost'] / holdings['shares']

            # 停牌時補前日市價
            holdings.loc[holdings['px_m'].isnull(), 'px_m'] = px_prev['px_m']
            holdings['mv']       = holdings['px_m'] * holdings['shares']
            holdings['unreal_pl'] = holdings['mv'] - holdings['cost']
            holdings['ret']       = holdings['unreal_pl'] / holdings['cost']
            holdings.fillna(0, inplace=True)
            holdings['tlt_pl'] = holdings['unreal_pl'] + holdings['real_pl'] + holdings['dvd']

            for col in perf_col_name:
                performance.loc[date, col] = holdings[col].sum()

        # ── 事後統計：計算日報酬、累積報酬 ──────────────────────────────────
        performance['p_ret'] = (
            (performance['tlt_pl'] - performance['tlt_pl'].shift(1)) /
            abs(performance['mv'].shift(1)))
        performance[['b_ret', 'b_O', 'b_L']] = df_index.loc[
            df_index['date'].isin(trade_dates_start.date),
            ['date', 'PX_Return', 'PX_OPEN', 'PX_LAST']
        ].set_index('date')

        # 修正起始期報酬（建倉初期分母為成本而非前日市值）
        performance.loc[performance['cost'].shift(1) == 0, 'p_ret'] = (
            (performance['tlt_pl'] - performance['tlt_pl'].shift(1)) / performance['cost'])
        performance.loc[(performance['cost'] == 0) & (performance['cost'].shift(1) == 0), 'p_ret'] = 0
        performance.loc[performance.index <= performance['cost'].ne(0).idxmax(), 'p_ret'] = (
            performance['tlt_pl'] / performance['cost'])
        performance.loc[performance.index <= performance['cost'].ne(0).idxmax(), 'b_ret'] = (
            (performance['b_L'] - (performance['b_O'] + performance['b_L']) * 0.5) /
            ((performance['b_O'] + performance['b_L']) * 0.5))

        performance['p_ret_cul'] = (performance['p_ret'] + 1).cumprod()
        performance['b_ret_cul'] = (performance['b_ret'] + 1).cumprod()
        performance = performance.loc[:, perf_col_name + ret_col_name]

        # 分年績效
        performance_year = performance.loc[
            performance.groupby(performance.index.year).tail(1).index,
            ['p_ret_cul', 'b_ret_cul']]
        performance_year['p_ret'] = (
            performance_year['p_ret_cul'] / performance_year['p_ret_cul'].shift(1)) - 1
        performance_year['b_ret'] = (
            performance_year['b_ret_cul'] / performance_year['b_ret_cul'].shift(1)) - 1
        performance_year.loc[performance_year.index[0], 'p_ret'] = (
            performance_year.loc[performance_year.index[0], 'p_ret_cul'] - 1)
        performance_year.loc[performance_year.index[0], 'b_ret'] = (
            performance_year.loc[performance_year.index[0], 'b_ret_cul'] - 1)
        performance_year['alpha'] = performance_year['p_ret'] - performance_year['b_ret']

        n_years = len(performance_year)
        performance_tlt.loc['p', 'ret'] = performance['p_ret_cul'].tail(1).values ** (1 / n_years) - 1
        if len(hold_portfolio) > 0:
            hold_portfolio = pd.concat(hold_portfolio, axis=0)
            trade = pd.concat(trade, axis=0)
        performance_tlt.loc['b', 'ret'] = performance['b_ret_cul'].tail(1).values ** (1 / n_years) - 1
        performance_tlt.loc['p', 'vol'] = np.std(performance['p_ret']) * 252 ** 0.5
        performance_tlt.loc['b', 'vol'] = np.std(performance['b_ret']) * 252 ** 0.5
        performance_tlt.loc['p', 'r/v'] = performance_tlt.loc['p', 'ret'] / performance_tlt.loc['p', 'vol']
        performance_tlt.loc['b', 'r/v'] = performance_tlt.loc['b', 'ret'] / performance_tlt.loc['b', 'vol']
        performance_tlt.loc['p', 'max_ret'] = performance['p_ret_cul'].max() - 1
        performance_tlt.loc['p', 'min_ret'] = performance['p_ret_cul'].min() - 1
        performance_tlt.loc['b', 'max_ret'] = performance['b_ret_cul'].max() - 1
        performance_tlt.loc['b', 'min_ret'] = performance['b_ret_cul'].min() - 1
        NAV_avg = performance_set[0] + holdings['tlt_pl'].mean()
        performance_tlt.loc['p', 'turn_over'] = (turnover / 2) / NAV_avg / len(trade_dates_start) * 250
        performance_tlt.loc['b', 'turn_over'] = 0
        performance_tlt.loc['p', 'MDD'] = self.max_dd(performance['p_ret'])
        performance_tlt.loc['b', 'MDD'] = self.max_dd(performance['b_ret'])
        performance_tlt.loc['p', 'stp_loss'] = count_stop_loss

        return performance_tlt, performance_year, performance, hold_portfolio, trade, holdings

    def performance_opt(self, signal_data, trade_dates_start, date_map, df_index, portfolio_1, *args):
        """
        最佳化專用回測引擎（精簡版）：僅回傳 performance_year，用於參數網格掃描。
        邏輯與 performance() 相同，但省略 hold_portfolio / trade 收集以加快速度。
        """
        performance_set = args[0]
        para = args[1]
        signal_data.sort_values(
            by=['date', 'signal', 'indicator'], ascending=[True, False, False], inplace=True)

        count_stop_loss = 0
        stock_pool = signal_data['stk_bbg'].unique()

        trade_col_name    = ['p_w', 'c_t_1', 't_shares', 'o_t', 'p_t', 't_amt']
        cor_act_col_name  = ['Adj_Factor_rev', 'DVD', 't']
        holdings_col_name = ['shares', 'px', 'cost', 'px_m', 'mv', 'unreal_pl', 'ret', 'dvd', 'real_pl', 'tlt_pl']
        holdings = pd.DataFrame(
            index=stock_pool,
            columns=holdings_col_name + trade_col_name + cor_act_col_name, data=0)
        holdings.index.name = 'stk_bbg'

        perf_col_name   = ['unreal_pl', 'real_pl', 'dvd', 'cost', 'mv', 'tlt_pl']
        ret_col_name    = ['p_ret', 'b_ret', 'p_ret_cul', 'b_ret_cul']
        ret_col_name_add = ['b_O', 'b_L']
        # 最後一天為最新訊號計算用，不納入回測迴圈
        performance = pd.DataFrame(
            index=trade_dates_start.date[0:-1],
            columns=perf_col_name + ret_col_name + ret_col_name_add, data=0)

        t_1 = date_map.loc[date_map['date_trade'] == trade_dates_start.date[0], 'date_trade_last']
        signal_data['PX_LAST_fill'] = signal_data.groupby('stk_bbg')['PX_LAST'].fillna(method='ffill')
        holdings['px_m'] = signal_data.loc[
            signal_data['date'] == t_1[0], ['stk_bbg', 'PX_LAST_fill']].set_index('stk_bbg')
        holdings['px_m'].fillna(0, inplace=True)

        df_stop_loss = pd.DataFrame(index=stock_pool, columns=['stp_loss'], data=0)

        for d, date in enumerate(trade_dates_start.date[0:-1]):
            signal_t = signal_data.loc[signal_data['date'] == date]
            holdings['t'] = signal_t.set_index('stk_bbg')['t']

            stop_loss_stock = holdings.loc[(holdings['ret'] < para[1]) & (holdings['t'] == 1)].index
            count_stop_loss += len(stop_loss_stock)
            df_stop_loss.loc[df_stop_loss.index.isin(stop_loss_stock), 'stp_loss'] = para[3]
            stop_loss_stock = df_stop_loss.loc[df_stop_loss['stp_loss'] > 0].index
            signal_t.loc[signal_t['stk_bbg'].isin(stop_loss_stock), 'signal'] = 0
            df_stop_loss.loc[df_stop_loss['stp_loss'] > 0, 'stp_loss'] -= 1

            n_1 = 0
            portfolio_hold = portfolio_1['stk_bbg']
            portfolio_hold = pd.merge(
                portfolio_hold, signal_t[['date', 'stk_bbg', 'signal', 'indicator', 't']],
                on=['stk_bbg'], how='left')
            portfolio_hold['signal'].fillna(0, inplace=True)
            portfolio_hold['date'].fillna(date, inplace=True)
            portfolio_hold.sort_values(['signal', 'indicator'], ascending=[False, False], inplace=True)
            portfolio_hold.reset_index(inplace=True, drop=True)
            hold_num = len(portfolio_hold)

            portfolio_others = signal_t.loc[
                ~signal_t['stk_bbg'].isin(portfolio_hold['stk_bbg']),
                ['date', 'stk_bbg', 'signal', 'indicator', 't']]
            portfolio_others.sort_values(['signal', 'indicator'], ascending=[False, False], inplace=True)
            portfolio_others = portfolio_others.reset_index(drop=True)

            if hold_num < para[0]:
                new_hold = portfolio_others.iloc[0:int(para[0] - hold_num)]
                new_hold = new_hold.loc[new_hold['t'] < 2]
                portfolio_hold = pd.concat([portfolio_hold, new_hold], ignore_index=True)
                portfolio_hold.reset_index(drop=True, inplace=True)
                portfolio_others = signal_t.loc[
                    ~signal_t['stk_bbg'].isin(portfolio_hold['stk_bbg']),
                    ['date', 'stk_bbg', 'signal', 'indicator', 't']]
                portfolio_others.sort_values(['signal', 'indicator'], ascending=[False, False], inplace=True)
                portfolio_others = portfolio_others.reset_index(drop=True)

            if len(portfolio_hold) > 0:
                for i, ind in enumerate(portfolio_others.indicator):
                    for i_h in range(n_1, len(portfolio_hold)):
                        if (portfolio_hold.loc[i_h, 't'] < 2) and (portfolio_others.loc[i, 't'] < 2):
                            if ((ind - portfolio_hold.loc[i_h, 'indicator'] > para[2] or
                                    portfolio_hold.loc[i_h, 'signal'] <= 0) and
                                    portfolio_others.loc[i, 'signal'] > 0):
                                for i_j in range(len(portfolio_hold) - 1, i_h, -1):
                                    portfolio_hold.loc[i_j, :] = portfolio_hold.loc[i_j - 1, :]
                                portfolio_hold.loc[i_h, :] = portfolio_others.loc[i, :]
                                n_1 = i_h + 1
                                break
                    if i_h == len(portfolio_hold) - 1:
                        break

            portfolio_1 = portfolio_hold.loc[portfolio_hold['signal'] > 0]

            px_prev = pd.DataFrame(holdings['px_m'])
            holdings[cor_act_col_name] = 0
            holdings[['o_t', 'px_m', 'vol', 'Adj_Factor_rev', 'DVD', 't']] = signal_t[
                ['stk_bbg', 'PX_OPEN', 'PX_LAST', 'PX_VOLUME', 'Adj_Factor_rev', 'DVD', 't']
            ].set_index('stk_bbg')
            holdings['DVD'].fillna(0, inplace=True)
            holdings['Adj_Factor_rev'].fillna(1, inplace=True)
            holdings['t'].fillna(2, inplace=True)

            holdings['shares'] = holdings['shares'] * holdings['Adj_Factor_rev']
            holdings['px']     = holdings['cost'] / holdings['shares']
            holdings['dvd']    = holdings['dvd'] + holdings['mv'] * holdings['DVD'] * (1 - performance_set[4])

            holdings['p_w_1'] = holdings['p_w']
            holdings['p_w']   = 0
            holdings.loc[portfolio_1['stk_bbg'], 'p_w'] = 1 / para[0]
            holdings['p_w_1'] = holdings['p_w'] - holdings['p_w_1']

            if abs(holdings['p_w_1']).sum() > 0:
                holdings['t_shares'] = round(
                    holdings['p_w_1'] * performance_set[0] / px_prev['px_m'] / performance_set[5], 0
                ) * performance_set[5]
                holdings['p_t'] = (holdings['o_t'] + holdings['px_m']) / 2
                holdings['t_shares'].fillna(0, inplace=True)
                holdings['t_shares'] = holdings['t_shares'] * holdings['Adj_Factor_rev']
                holdings.loc[holdings['p_w_1'] < 0, 't_shares'] = -holdings['shares']

                holdings['t_amt'] = holdings['t_shares'] * holdings['p_t']
                holdings.loc[holdings['t_shares'] > 0, 't_amt'] *= (1 + performance_set[1] + performance_set[2])
                holdings.loc[holdings['t_shares'] < 0, 't_amt'] *= (
                    1 - performance_set[1] - performance_set[2] - performance_set[3])

                holdings['shares'] += holdings['t_shares']
                holdings.loc[holdings['t_shares'] > 0, 'cost'] += holdings['t_amt']
                holdings.loc[holdings['t_shares'] < 0, 'cost'] += holdings['t_shares'] * holdings['px']
                holdings.loc[holdings['shares'] == 0, 'cost'] = 0
                holdings.loc[holdings['t_shares'] < 0, 'real_pl'] += (
                    -holdings['t_amt'] + holdings['t_shares'] * holdings['px'])
                holdings['px'] = holdings['cost'] / holdings['shares']

            holdings.loc[holdings['px_m'].isnull(), 'px_m'] = px_prev['px_m']
            holdings['mv']        = holdings['px_m'] * holdings['shares']
            holdings['unreal_pl'] = holdings['mv'] - holdings['cost']
            holdings['ret']       = holdings['unreal_pl'] / holdings['cost']
            holdings.fillna(0, inplace=True)
            holdings['tlt_pl'] = holdings['unreal_pl'] + holdings['real_pl'] + holdings['dvd']

            for col in perf_col_name:
                performance.loc[date, col] = holdings[col].sum()

        # ── 年度績效彙總 ──────────────────────────────────────────────────────
        performance['p_ret'] = (
            (performance['tlt_pl'] - performance['tlt_pl'].shift(1)) /
            abs(performance['mv'].shift(1)))
        performance[['b_ret', 'b_O', 'b_L']] = df_index.loc[
            df_index['date'].isin(trade_dates_start.date),
            ['date', 'PX_Return', 'PX_OPEN', 'PX_LAST']
        ].set_index('date')

        performance.loc[performance['cost'].shift(1) == 0, ['p_ret', 'b_ret']] = 0
        performance.loc[performance.index <= performance['cost'].ne(0).idxmax(), ['p_ret', 'b_ret']] = 0
        performance.loc[
            (performance['cost'].shift(periods=2, fill_value=0) == 0) &
            (performance['cost'].shift(1) > 0), 'p_ret'
        ] = ((performance['tlt_pl'] - performance['tlt_pl'].shift(periods=2, fill_value=0)) /
             performance['mv'].shift(1))
        performance.loc[
            (performance['cost'].shift(periods=2, fill_value=0) == 0) &
            (performance['cost'].shift(1) > 0), 'b_ret'
        ] = ((performance['b_L'] - (performance['b_O'].shift(1) + performance['b_L'].shift(1)) / 2) /
             ((performance['b_O'].shift(1) + performance['b_L'].shift(1)) / 2))

        performance['p_ret_cul'] = (performance['p_ret'] + 1).cumprod()
        performance['b_ret_cul'] = (performance['b_ret'] + 1).cumprod()
        performance = performance.loc[:, perf_col_name + ret_col_name]

        performance_year = performance.loc[
            performance.groupby(performance.index.year).tail(1).index,
            ['p_ret_cul', 'b_ret_cul']]
        performance_year['p_ret'] = (
            performance_year['p_ret_cul'] / performance_year['p_ret_cul'].shift(1)) - 1
        performance_year['b_ret'] = (
            performance_year['b_ret_cul'] / performance_year['b_ret_cul'].shift(1)) - 1
        performance_year.loc[performance_year.index[0], 'p_ret'] = (
            performance_year.loc[performance_year.index[0], 'p_ret_cul'] - 1)
        performance_year.loc[performance_year.index[0], 'b_ret'] = (
            performance_year.loc[performance_year.index[0], 'b_ret_cul'] - 1)
        performance_year['alpha'] = performance_year['p_ret'] - performance_year['b_ret']

        return performance_year
