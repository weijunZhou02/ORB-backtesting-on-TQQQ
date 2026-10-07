import pandas as pd
import datetime as dt
raw = pd.read_csv('TQQQ_5min.csv', parse_dates=['timestamp'], index_col='timestamp')

# One day, wide window so we can see where the volume jumps
print(raw.loc['2016-01-05'].between_time('08:00', '10:30')[['Open', 'Close', 'Volume']])

# First and last bar time on each day: which pattern is most common?
first_last = raw.groupby(raw.index.date).apply(lambda b: (b.index[0].time(), b.index[-1].time()))
print(first_last.value_counts().head())


