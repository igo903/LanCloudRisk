# Wyckoff research module v1.0

Wyckoff analysis is an opt-in research factor in the smile/cry backtest at `#backtest/<code>`. Old `#wyckoff/<code>` bookmarks lead to that asset’s backtest. The factor is off by default and uses the selected backtest dates; the existing signal formula and execution remain available without it.

## Method and interpretation

Reference: [Wyckoff Analytics — Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/), consulted 2026-11-05. Supply/demand, effort/result, Springs, tests, SOS and LPS inform these research proxies. Numeric thresholds below are our explicit heuristics, not canonical Wyckoff rules or calibrated trading recommendations. P&F cause/effect counts, target prices, complete A–E phase classification, SC/BC and institutional identity are not inferred.

- Prior range: highest high / lowest low of the preceding 20, 40 (default), or 60 **closed** bars, excluding the candidate bar.
- Volume ratio and spread ratio: candidate volume and high–low spread divided by the preceding 20-bar mean, excluding the candidate. Any missing/zero volume in that window disables the event. Volume is in the provider-declared unit; within-symbol ratios do not compare absolute units between markets.
- Spring / UT: penetrate prior support/resistance by over 0.5%, close inside the range and in the top/bottom 40% of the candle. Prior range width must be at most 35%; this is only a range proxy, not proof of accumulation/distribution.
- SOS / SOW: close past the prior range by over 0.5%, volume ratio ≥1.5, spread ratio ≥1.2, directional candle and close in the top/bottom 30%.
- Test: preceding Spring within 10 bars, higher low near its support, no intervening close below the Spring low, volume less than the Spring and <0.8× the prior mean, narrower spread and upper-half close.
- LPS: preceding SOS within 10 bars, revisit old resistance ±2%, close above it, no intervening close more than 2% below it, volume <0.8× and spread <1×.
- Events require at least 61 closed bars. Trend context uses MA20, MA60 and the five-bar slope of MA60. The slope needs 65 bars; shorter histories cannot meet its directional trend condition. Up-day/down-day volume ratio uses the last 20 closed days classified by close versus prior close, excluding flat days; zero down-day volume yields missing, not infinity.

Candidates are dated at the candidate close. They are validated/failed using only the **next three closed bars available as of the chosen date**. A bullish candidate fails on a close below its low, bearish on a close above its high. Spring/Test/LPS validate above their high; UT below its low; SOS/SOW beyond their original close in the breakout direction. First validation/failure wins within the window; unresolved candidates expire unconfirmed after three bars. Validation is a historical follow-through observation, not ongoing validity or an investment outcome. Candidate dates and observation dates remain separate. Replaying to before a confirmation removes that confirmation.

## Data and failure states

Inputs are existing provider OHLCV and per-bar completion flags. When the source uses record-level completion, the existing confirmed-history accessor resolves it before analysis. Intraday bars remain visible but are excluded from every structural calculation and confirmation; their raw volume is not compared to full days. The analysis cutoff is applied before any calculation. Invalid OHLC/date ordering blocks interpretation rather than silently repairing it. Missing volume, short history, unknown units/adjustments and provider failures are explicit. Unadjusted corporate actions, suspension and price limits may produce false events; the module does not infer adjusted history or synthesize missing trading days. Synthetic mode is labelled separately. There is no new polling or provider request, and an old snapshot is not described as today's market.

## Integration with smile/cry signals

- Only validated events inform the factor. The activation date is the validation close, never the candidate date. The validation close must have usable relative volume to change a signal that day.
- A same-day bullish validation adds a B smile if there is no original signal. A same-day bearish validation adds a strong cry when there is no original sell, replacing an opposite smile if present.
- Same-direction signals in the validation session and following two closed sessions are marked as supported. Within that window, an opposite smile is filtered by bearish evidence. A newer validation supersedes the previous one; same-day opposing validations prioritize the bearish direction. Existing sell signals survive contrary bullish evidence. Original three-factor buy vetoes, C-grade skips and the A-share six-ETF gate still apply. Missing volume leaves the original signal unchanged.
- One effective signal per date enters the existing next-session execution and fee/cycle accounting. The chart and signal details use that same plan; original provisional markers are displayed without Wyckoff adjustment. Signals on the final day do not trade outside the interval.
- A blue triangle stacks beneath each supported smile/cry marker, including the factor's new signals. The separate explanation table shows signal date, candidate and validation dates, volumes, spread, price test, effect on the strategy and the actual simulated order status. Its date buttons focus the existing chart. The optional prior-range overlay adds no separate chart.

Tests cover causal windows, event chains, missing/zero volume, validation/failure/expiry, provisional exclusion, cutoff replay, market gates, next-session execution, exit preservation, and marker construction. Numeric rules are research hypotheses rather than claims of improved future returns.
