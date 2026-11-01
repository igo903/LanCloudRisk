# Third-party notices

This application bundles Lightweight Charts 5.1.0 from TradingView.

- Project: https://github.com/tradingview/lightweight-charts
- License: Apache License 2.0; see `dist/lightweight-charts.LICENSE.txt`.
- The upstream notice and in-chart TradingView attribution are retained.

The library is embedded in `run_risklens_local.py` for offline asset loading. Market data is fetched separately from the publicly accessible provider endpoints described in the UI. Bundling this application does not grant additional rights to third-party data.

## Geopolitical Risk (GPR) data

Source: https://www.matteoiacoviello.com/gpr.htm
Daily Stata dataset: https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.dta
Credit: Dario Caldara and Matteo Iacoviello (2022), “Measuring Geopolitical Risk,” American Economic Review, 112(4), 1194–1225.
The source publishes its data under Creative Commons Attribution 4.0. The application derives 30-day rolling averages and historical percentile attention scores from GPRD_THREAT and GPRD_ACT; these transformations are not probabilities and are not endorsed by the authors. Data may be revised.

Global market series are requested from Yahoo Finance, with Tencent (HSTECH) and Eastmoney (USDCNH and FTSE China A50/XIN9) historical fallbacks identified per row. Provider data rights remain with their respective owners. Gold/silver continuous futures and Treasury yields are explicitly distinguished from cash assets and investment returns.
