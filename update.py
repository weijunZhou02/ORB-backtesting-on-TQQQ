import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt

# Out-of-sample update of backtest.py: same ORB rules, data after the paper's sample.
START = '2023-02-21'                                             # first trading day after backtest.py's sample
END = (dt.date.today() - dt.timedelta(days=1)).isoformat()       # yesterday
NEW_CSV = 'TQQQ_5min_new.csv'

# Read ALPACA_API_KEY / ALPACA_SECRET_KEY from a .env file next to this script
if os.path.exists('.env'):
    for line in open('.env'):
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.strip().split('=', 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


# --- 0. Download the new data ------------------------------------------
def get_json(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', **(headers or {})})
    for attempt in range(6):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:          # bad key / bad request: no point retrying
                raise RuntimeError(f"{e.code} {e.read().decode()[:200]}") from None
        except Exception:                               # dropped connection / timeout
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"gave up after 6 tries: {url.split('?')[0]}")


def to_ny(index):
    return index.tz_convert('America/New_York').tz_localize(None)


def alpaca_5min(symbol, start, end):
    # Full history, needs free keys from alpaca.markets in ALPACA_API_KEY / ALPACA_SECRET_KEY
    headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY'],
               'APCA-API-SECRET-KEY': os.environ['ALPACA_SECRET_KEY']}
    # free plan refuses SIP data from the last 15 minutes, so the end must be an explicit, older time
    end = min(pd.Timestamp(end + ' 16:00', tz='America/New_York'),
              pd.Timestamp.now(tz='America/New_York') - pd.Timedelta(minutes=20)).isoformat()
    params = dict(timeframe='5Min', start=start, end=end, adjustment='split', feed='sip', limit=10000)
    bars = []
    while True:
        j = get_json(f'https://data.alpaca.markets/v2/stocks/{symbol}/bars?' + urllib.parse.urlencode(params), headers)
        bars += j.get('bars') or []
        print(f"  downloaded {len(bars)} bars, up to {bars[-1]['t'][:10]}", flush=True)
        if not j.get('next_page_token'):
            break
        params['page_token'] = j['next_page_token']
    out = pd.DataFrame(bars).rename(columns=dict(t='timestamp', o='Open', h='High', l='Low', c='Close', v='Volume'))
    out['timestamp'] = pd.to_datetime(out['timestamp'], utc=True)
    out = out.set_index('timestamp')[['Open', 'High', 'Low', 'Close', 'Volume']]
    out.index = to_ny(out.index)
    return out


def yahoo(symbol, interval, start=None):
    # No key needed, but Yahoo only serves the last 60 days of 5-minute bars
    if interval == '5m':
        query = 'range=60d&interval=5m&includePrePost=false'
    else:
        query = f'period1={int(pd.Timestamp(start).timestamp())}&period2={int(time.time())}&interval={interval}'
    r = get_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}')['chart']['result'][0]
    q = r['indicators']['quote'][0]
    out = pd.DataFrame(dict(Open=q['open'], High=q['high'], Low=q['low'], Close=q['close'], Volume=q['volume']),
                       index=pd.to_datetime(r['timestamp'], unit='s', utc=True))
    out.index = to_ny(out.index)
    out.index.name = 'timestamp'
    return out.dropna()


if os.environ.get('ALPACA_API_KEY'):
    new = alpaca_5min('TQQQ', START, END)
    source = 'Alpaca'
else:
    new = yahoo('TQQQ', '5m')
    source = 'Yahoo (last 60 days only)'

new = new.between_time('09:30', '15:55')          # regular session, same as TQQQ_5min.csv
if os.path.exists(NEW_CSV):                       # keep what earlier runs already saved
    old = pd.read_csv(NEW_CSV, parse_dates=['timestamp'], index_col='timestamp')
    new = new.combine_first(old)
new = new.sort_index().loc[START:END]
new.to_csv(NEW_CSV)
print(f"Data source: {source}")
print(f"Saved {NEW_CSV}: {len(new)} bars, {new.index[0]} -> {new.index[-1]}")

df = new

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
print("Total return %:", round((A / START_CAPITAL - 1) * 100, 1))
print("Days capped by leverage %:", round(equity.capped.mean() * 100, 1))

print(signals.reason.value_counts())
print(signals.pnl_R.describe())

print("Win rate %:", round((signals.pnl_R > 0).mean() * 100, 1))

days = pd.to_datetime(sorted(set(df.index.date)))

orb = equity.equity.reindex(days).ffill().fillna(START_CAPITAL)

# QQQ benchmark from daily closes (no new QQQ 5-minute file needed)
qqq = yahoo('QQQ', '1d', days[0]).Close
qqq.index = qqq.index.normalize()
qqq = qqq.reindex(days).ffill()

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

# --- Equity curve chart -------------------------------------------------
fig, ax = plt.subplots(figsize=(10, 6))
ax.plot(orb, color='black', label='ORB TQQQ')
ax.plot(bh_tqqq, color='deepskyblue', linestyle='--', label='Buy&Hold TQQQ')
ax.plot(bh_qqq, color='red', linestyle='--', label='Buy&Hold QQQ')

ax.set_yscale('log')
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
ax.set_title(f"5-min ORB on TQQQ vs Buy & Hold ({days[0]:%b %Y} – {days[-1]:%b %Y})")
ax.legend()
ax.grid(alpha=0.3)
plt.tight_layout()
plt.savefig('orb_equity_update.png', dpi=150)
plt.show()
