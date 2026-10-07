import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import datetime as dt

# Same ORB rules as backtest.py / update.py, run on a list of other tickers.
START = '2023-02-21'
END = (dt.date.today() - dt.timedelta(days=1)).isoformat()
DATA_DIR = 'data_stocks'
SYMBOLS = ['TQQQ', 'SQQQ', 'SOXL', 'QQQ', 'SPY', 'IWM',
           'NVDA', 'TSLA', 'AMD', 'AAPL', 'MSFT', 'AMZN', 'META', 'GOOGL', 'NFLX',
           'PLTR', 'COIN', 'MSTR']

START_CAPITAL = 25000
RISK_PCT = 0.01
MAX_LEV = 4
COMM_RATE = 0.00001

if os.path.exists('.env'):
    for line in open('.env'):
        if '=' in line and not line.strip().startswith('#'):
            k, v = line.strip().split('=', 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"\''))


def get_json(url, headers):
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(8):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise RuntimeError(f"{e.code} {e.read().decode()[:200]}") from None
        except Exception:
            pass
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"gave up: {url.split('?')[0]}")


def load(symbol):
    path = f'{DATA_DIR}/{symbol}_5min.csv'
    if os.path.exists(path):
        return pd.read_csv(path, parse_dates=['timestamp'], index_col='timestamp')
    headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY'],
               'APCA-API-SECRET-KEY': os.environ['ALPACA_SECRET_KEY']}
    end = min(pd.Timestamp(END + ' 16:00', tz='America/New_York'),
              pd.Timestamp.now(tz='America/New_York') - pd.Timedelta(minutes=20)).isoformat()
    params = dict(timeframe='5Min', start=START, end=end, adjustment='split', feed='sip', limit=10000)
    bars = []
    while True:
        j = get_json(f'https://data.alpaca.markets/v2/stocks/{symbol}/bars?' + urllib.parse.urlencode(params), headers)
        bars += j.get('bars') or []
        if not j.get('next_page_token'):
            break
        params['page_token'] = j['next_page_token']
    out = pd.DataFrame(bars).rename(columns=dict(t='timestamp', o='Open', h='High', l='Low', c='Close', v='Volume'))
    out['timestamp'] = pd.to_datetime(out['timestamp'], utc=True)
    out = out.set_index('timestamp')[['Open', 'High', 'Low', 'Close', 'Volume']]
    out.index = out.index.tz_convert('America/New_York').tz_localize(None)
    out = out.between_time('09:30', '15:55')
    out.to_csv(path)
    print(f"  downloaded {symbol}: {len(out)} bars", flush=True)
    return out


def orb_trades(df):
    signals = []
    for day, bars in df.groupby(df.index.date):
        if len(bars) < 2 or bars.index[0].time() != dt.time(9, 30):
            continue
        first = bars.iloc[0]
        second = bars.iloc[1]
        if first.Close > first.Open:
            direction = 1
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
        target = entry + direction * 10 * R
        exit_price, reason = bars.iloc[-1].Close, 'eod'
        for high, low in zip(bars.High.values[1:], bars.Low.values[1:]):
            if direction == 1:
                hit_stop, hit_target = low <= stop, high >= target
            else:
                hit_stop, hit_target = high >= stop, low <= target
            if hit_stop:
                exit_price, reason = stop, 'stop'
                break
            if hit_target:
                exit_price, reason = target, 'target'
                break
        signals.append(dict(date=pd.Timestamp(day), direction=direction, entry=entry, stop=stop, R=R,
                            exit_price=exit_price, reason=reason, pnl_R=direction * (exit_price - entry) / R))
    return pd.DataFrame(signals).set_index('date')


def equity_curve(signals, days):
    A = START_CAPITAL
    rows = []
    for day, tr in signals.iterrows():
        shares = min(A * RISK_PCT / tr.R, MAX_LEV * A / tr.entry)
        pnl = shares * tr.direction * (tr.exit_price - tr.entry) - shares * (tr.entry + tr.exit_price) * COMM_RATE
        A += pnl
        rows.append(dict(date=day, equity=A))
    eq = pd.DataFrame(rows).set_index('date').equity
    return pd.concat([pd.Series([START_CAPITAL], index=[days[0] - pd.Timedelta(days=1)]), eq.reindex(days).ffill().fillna(START_CAPITAL)])


def stats(curve):
    ret = curve.pct_change().dropna()
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    growth = curve.iloc[-1] / curve.iloc[0]
    return growth - 1, growth ** (1 / years) - 1, ret.mean() / ret.std() * 252 ** 0.5, (curve / curve.cummax() - 1).min() * -1


if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    with ThreadPoolExecutor(4) as pool:
        data = dict(zip(SYMBOLS, pool.map(load, SYMBOLS)))

    rows = []
    for symbol, df in data.items():
        df = df.loc[START:END]
        signals = orb_trades(df)
        signals.to_csv(f'{DATA_DIR}/{symbol}_trades.csv')
        days = pd.to_datetime(sorted(set(df.index.date)))
        tot, cagr, sharpe, mdd = stats(equity_curve(signals, days))
        close = df.Close.groupby(df.index.date).last()
        close.index = pd.to_datetime(close.index)
        bh_tot, bh_cagr, bh_sharpe, bh_mdd = stats(close)
        rows.append({'Symbol': symbol, 'Trades': len(signals),
                     'Avg R': round(signals.pnl_R.mean(), 3),
                     'Win %': round((signals.pnl_R > 0).mean() * 100, 1),
                     'Target %': round((signals.reason == 'target').mean() * 100, 1),
                     'Long R': round(signals.pnl_R[signals.direction == 1].mean(), 2),
                     'Short R': round(signals.pnl_R[signals.direction == -1].mean(), 2),
                     'ORB Total %': round(tot * 100), 'ORB Yearly %': round(cagr * 100),
                     'ORB Sharpe': round(sharpe, 2), 'ORB MDD %': round(mdd * 100),
                     'B&H Total %': round(bh_tot * 100), 'B&H Sharpe': round(bh_sharpe, 2), 'B&H MDD %': round(bh_mdd * 100)})

    table = pd.DataFrame(rows).set_index('Symbol').sort_values('Avg R', ascending=False)
    pd.set_option('display.width', 250)
    pd.set_option('display.max_columns', 30)
    print(table)
    table.to_csv('orb_stocks_results.csv')
