import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt
from backtesting import Strategy, Backtest
df = pd.read_csv('TQQQ_5min.csv',parse_dates=['timestamp'],index_col='timestamp')

df = df.loc['2016-01-01':'2023-02-17']

bars_per_day = df.groupby(df.index.date).size()

def find_exit(bars, direction, entry, stop,R, target_R=10):
    target = entry +direction * target_R * R
    for t, bar in bars.iloc[1:].iterrows():
        if direction == 1:
            hit_stop = bar.Low <= stop
            hit_target = bar.High >= target
        else:
            hit_stop = bar.High >= stop
            hit_target = bar.Low <= target
        if hit_stop:
            return t, stop, 'stop'
        if hit_target:
            return t, target, 'target'

    return bars.index[-1],bars.iloc[-1].Close,'eod'
signals = []

for day,bars in df.groupby(df.index.date):
    if len(bars) < 2 or bars.index[0].time() != dt.time(9, 30):
        continue
    first = bars.iloc[0]
    second = bars.iloc[1]

    if first.Close > first.Open:
        direction =1
        stop = first.Low
    elif first.Close < first.Open:
        direction = -1
        stop = first.High
    else:
        continue



    entry = second.Open
    R = direction * (entry - stop)

    if R <= 0:
        continue

    exit_time, exit_price, reason = find_exit(bars, direction, entry, stop,R)
    # exit_time = bars.index[-1]  # 15:55 candle
    # exit_price = bars.iloc[-1].Close  # close of the day
    # reason = 'eod'
    # pnl_R = direction * (exit_price - entry) / R
    # signals.append(dict(date=day, direction=direction, entry=entry, stop=stop, R=R,
    #                     exit_price=exit_price, reason=reason, pnl_R=pnl_R))
    pnl_R = direction * (exit_price - entry)/R
    signals.append(dict(date=day, direction=direction, entry=entry, stop=stop, R=R,
                        exit_price=exit_price, reason=reason, pnl_R=pnl_R))

signals = pd.DataFrame(signals).set_index('date')

START_CAPITAL = 25000
RISK_PCT = 0.01
MAX_LEV = 4
COMM_RATE = 0.00001

A = START_CAPITAL
rows = []
for day, tr in signals.iterrows():
    risk_shares = A * RISK_PCT / tr.R
    lev_shares = MAX_LEV * A / tr.entry
    shares = min(risk_shares, lev_shares)

    gross = shares * tr.direction * (tr.exit_price - tr.entry)
    commission = shares*(tr.entry + tr.exit_price) * COMM_RATE
    pnl = gross - commission
    A += pnl
    rows.append(dict(date = day, shares = shares, pnl=pnl, equity=A,capped = lev_shares < risk_shares))

equity = pd.DataFrame(rows).set_index('date')
equity.index = pd.to_datetime(equity.index)

print("Final equity: $", round(A))
print("Total return %:", round((A / START_CAPITAL - 1) * 100))
print("Days capped by leverage %:", round(equity.capped.mean() * 100, 1))


print(signals.reason.value_counts())
print("Win rate %: ", (signals.pnl_R > 0).mean()*100,1)
print(signals.pnl_R.describe())

print("Win rate %:", round((signals.pnl_R > 0).mean() * 100, 1))

days = pd.to_datetime(sorted(set(df.index.date)))

orb = equity.equity.reindex(days).ffill().fillna(START_CAPITAL)

qqq = pd.read_csv('QQQ_5min.csv',parse_dates=['timestamp'],index_col='timestamp').Close.loc[days[0]:days[-1]]

bh_qqq = START_CAPITAL * (qqq / qqq.iloc[0])

tqqq = df.Close.groupby(df.index.date).last()
tqqq.index = pd.to_datetime(tqqq.index)
bh_tqqq = START_CAPITAL * (tqqq / tqqq.iloc[0])

def stats(curve):
    ret = curve.pct_change().dropna()
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    growth = curve.iloc[-1] / curve.iloc[0]
    return {
        'Total Return': f"{(growth - 1) * 100:,.0f}%",
        'Yearly Return': f"{(growth ** (1 / years) - 1) * 100:.0f}%",
        'Volatility': f"{ret.std() * 252 ** 0.5 * 100:.0f}%",
        'Sharpe Ratio': f"{ret.mean() / ret.std() * 252 ** 0.5:.2f}",
        'MDD': f"{(curve / curve.cummax() - 1).min() * -100:.0f}%",
    }

table = pd.DataFrame({
    'ORB TQQQ': stats(orb),
    'Buy & Hold TQQQ': stats(bh_tqqq),
    'Buy & Hold QQQ': stats(bh_qqq),
}).T
print(table)

# --- 3. Equity curve chart (like Figure 5) -----------------------------
fig, ax = plt.subplots(figsize=(10, 6))
ax.plot(orb, color='black', label='ORB TQQQ')
ax.plot(bh_tqqq, color='deepskyblue', linestyle='--', label='Buy&Hold TQQQ')
ax.plot(bh_qqq, color='red', linestyle='--', label='Buy&Hold QQQ')

ax.axvspan('2020-02-19', '2020-03-23', color='grey', alpha=0.2)   # COVID crash
ax.axvspan('2022-01-03', '2022-12-28', color='grey', alpha=0.2)   # 2022 bear market

ax.set_yscale('log')
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
ax.set_title('5-min ORB on TQQQ vs Buy & Hold (2016 – Feb 2023)')
ax.legend()
ax.grid(alpha=0.3)
plt.tight_layout()
plt.savefig('orb_equity.png', dpi=150)
plt.show()

# class OpeningRangeBreakout(Strategy):
#     open_range_minutes = 5
#     last_minute_bar_in_opening_range = dt.time(9,30)
#     def init(self):
#         self.current_day = None
#         self.opening_range_high = None
#         self.opening_range_low = None
#
#     def _reset_range(self,day):
#         self.current_day = day
#         self.opening_range_high = None
#         self.opening_range_low = None
#     def next(self):
#         t = self.data.index[-1]
#         current_bar_date = t.date()
#
#         print(t)
#         print(current_bar_date)
#
#         if current_bar_date != self.current_day:
#             self._reset_range(current_bar_date)
#             print(f"new date: {current_bar_date}")
#
#         if t.time()<= self.last_minute_bar_in_opening_range:
#             print(f"{t.time()} is less than or equal to {self.last_minute_bar_in_opening_range}")
#             if self.opening_range_high is None or self.data.High[-1] > self.opening_range_high:
#                 self.opening_range_high = self.data.High[-1]
#             if self.opening_range_low is None or self.data.Low[-1] < self.opening_range_low:
#                 self.opening_range_low = self.data.Low[-1]
#             print(f"opening range high: {self.opening_range_high}, opening range low: {self.opening_range_low}")
#         else:
#             if self.opening_range_high is not None and self.data.Close[-1] > self.opening_range_high:
#                 print(f"Breakout above opening range high: {self.opening_range_high}")
#                 self.buy()
#             elif self.opening_range_low is not None and self.data.Close[-1] < self.opening_range_low:
#                 print(f"Breakdown below opening range low: {self.opening_range_low}")
#                 self.sell()
#
# df = df.loc['2016-01-05':'2016-01-06']
# bt = Backtest(df, OpeningRangeBreakout, cash=25000, commission=.002)
#
# stats = bt.run()
# bt.plot()
#
# print(stats)