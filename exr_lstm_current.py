import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import requests
import re
from datetime import datetime, timedelta
from io import StringIO
from pytrends.request import TrendReq
from sklearn.preprocessing import MinMaxScaler
from sklearn.metrics import mean_absolute_error, mean_absolute_percentage_error
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense
from tensorflow.keras.callbacks import EarlyStopping

# Streamlit page config
st.set_page_config(page_title="پیش‌بینی نرخ دلار آزاد تهران با LSTM 📈", layout="wide")
st.markdown("""
---
📈 © 2025 Dr. Farhadi. All rights reserved.  
This application was developed by **Dr. Farhadi**, Ph.D. in *Economics (Econometrics)* and *Data Science*.  
All trademarks and intellectual property are protected. ™
""")
st.title("📈 پیش‌بینی نرخ دلار آزاد با LSTM 📈")

# Constants
github_trends_url = (
    'https://raw.githubusercontent.com/AZFARHAD24511/exchange_rates_IRAN/main/'
    'predict/google_trends_daily_exrusd.csv'
)
KEYWORDS = ['خرید دلار', 'فروش دلار', 'دلار فردایی']

# Load historical USD data
def load_usd_data():
    ts = int(datetime.now().timestamp() * 1000)
    url = (
        f"https://api.tgju.org/v1/market/indicator/"
        f"summary-table-data/price_dollar_rl?period=all&mode=full&ts={ts}"
    )
    r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
    data = r.json().get('data', [])
    records = []
    for row in data:
        try:
            price = float(re.sub(r'<[^>]*>', '', row[0]).replace(',', ''))
            date = datetime.strptime(row[6], "%Y/%m/%d")
            records.append({'date': date, 'price': price})
        except:
            continue
    df = pd.DataFrame(records).set_index('date').sort_index()
    return df

# Load Google Trends data
def load_trends_csv():
    r = requests.get(github_trends_url)
    df = pd.read_csv(StringIO(r.text), parse_dates=['date'])
    return df.set_index('date').sort_index()

# Fetch missing trend data
@st.cache_data(ttl=3600, hash_funcs={pd.DatetimeIndex: lambda idx: idx.astype(str).tolist()})
def fetch_missing_trends(missing_dates, geo='IR'):
    if not isinstance(missing_dates, pd.DatetimeIndex):
        missing_dates = pd.to_datetime(list(missing_dates))
    pytrends = TrendReq(hl='fa', tz=330)
    df_list = []
    start, end = missing_dates.min(), missing_dates.max()
    timeframe = f"{start.strftime('%Y-%m-%d')} {end.strftime('%Y-%m-%d')}"
    for kw in KEYWORDS:
        pytrends.build_payload([kw], timeframe=timeframe, geo=geo)
        tmp = pytrends.interest_over_time()
        if not tmp.empty:
            df_list.append(tmp[kw].rename(kw))
    if df_list:
        df_new = pd.concat(df_list, axis=1).loc[missing_dates]
        return df_new.apply(lambda x: x / x.max() * 100)
    return pd.DataFrame(index=missing_dates)

# Load today's data and compute average of last 5 prices
def load_today_avg():
    url = "https://api.tgju.org/v1/market/indicator/today-table-data/price_dollar_rl"
    params = {
        "lang": "fa", "draw": 1, "start": 0, "length": 30,
        "today_table_tolerance_open": 1,
        "today_table_tolerance_yesterday": 1,
        "today_table_tolerance_range": "week",
        "_": int(pd.Timestamp.now().timestamp() * 1000)
    }
    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json().get('data', [])
    prices = [int(re.sub(r'<[^>]*>', '', row[0]).replace(',', '')) for row in data]
    last5 = prices[:5]
    return float(sum(last5) / len(last5)) if last5 else np.nan

# Load and prepare data
with st.spinner("در حال بارگذاری داده‌ها..."):
    usd_df = load_usd_data()
    # Append today's average price
    today = datetime.now().date()
    avg_price = load_today_avg()
    if not np.isnan(avg_price):
        usd_df.loc[pd.to_datetime(today)] = avg_price
    trends_df = load_trends_csv()

# Use last 2 years
two_years_ago = datetime.now() - timedelta(days=730)
udf = usd_df[usd_df.index >= two_years_ago]
trf = trends_df[trends_df.index >= two_years_ago]

# Fill missing trend dates
missing = udf.index.difference(trf.index)
if not missing.empty:
    new_tr = fetch_missing_trends(missing)
    trf = pd.concat([trf, new_tr]).sort_index()
    trf = trf.reindex(udf.index).ffill().bfill()

# Merge datasets
df = pd.merge(udf, trf, left_index=True, right_index=True, how='inner').ffill().bfill()
series = df['price'].values.reshape(-1, 1)
dates = df.index

# Scale data
scaler = MinMaxScaler()
scaled = scaler.fit_transform(series)

# Create sequences
SEQ_LEN = 30
def create_sequences(data, seq_length=SEQ_LEN):
    X, y = [], []
    for i in range(seq_length, len(data)):
        X.append(data[i-seq_length:i])
        y.append(data[i])
    return np.array(X), np.array(y)
X, y = create_sequences(scaled)

# Train-test split
split = int(len(X) * 0.9)
X_train, X_test = X[:split], X[split:]
y_train, y_test = y[:split], y[split:]

# Build and train LSTM model
model = Sequential([
    LSTM(64, input_shape=(SEQ_LEN, 1)),
    Dense(16, activation='relu'),
    Dense(1)
])
model.compile(optimizer='adam', loss='mse')
early = EarlyStopping(monitor='val_loss', patience=10, restore_best_weights=True)
model.fit(
    X_train, y_train,
    validation_data=(X_test, y_test),
    epochs=50,
    batch_size=16,
    callbacks=[early],
    verbose=0
)

# Predictions and metrics
preds = model.predict(X_test)
preds_inv = scaler.inverse_transform(preds)
y_test_inv = scaler.inverse_transform(y_test)
mae = mean_absolute_error(y_test_inv, preds_inv)
mape = mean_absolute_percentage_error(y_test_inv, preds_inv) * 100

# Forecast next 2 days
def forecast_next(model, last_seq, steps=2):
    seq = last_seq.copy()
    res = []
    for _ in range(steps):
        pred = model.predict(seq.reshape(1, SEQ_LEN, 1))
        res.append(pred)
        seq = np.concatenate([seq[1:], pred], axis=0)
    return np.array(res)
last_sequence = scaled[-SEQ_LEN:]
forecast_scaled = forecast_next(model, last_sequence, steps=2).reshape(-1, 1)
forecast = scaler.inverse_transform(forecast_scaled).flatten()
forecast_dates = [dates[-1] + timedelta(days=i) for i in range(1, 3)]

# Display results
st.info(f"MAE: {mae:,.2f}    MAPE: {mape:.2f}%")
for d, v in zip(forecast_dates, forecast):
    st.success(f"🔮 نرخ دلار برای {d.date()}: {v:,.0f} ریال")

# Plot
st.subheader("📊 Historical & LSTM Forecast")
fig, ax = plt.subplots(figsize=(12, 6))
ax.plot(dates, series.flatten(), label='Historical')
ax.axvline(dates[-1], linestyle='--')
for i, (d, v) in enumerate(zip(forecast_dates, forecast), 1):
    ax.scatter(d, v)
    ax.annotate(f'Day+{i}: {v:,.0f}', xy=(d, v), xytext=(0,10), textcoords='offset points', ha='center', arrowprops=dict(arrowstyle='->'))
ax.set_title('USD Free Market Rate LSTM Forecast')
ax.grid(True)
st.pyplot(fig)
