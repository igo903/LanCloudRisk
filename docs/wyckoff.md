# Wyckoff research module

Wyckoff analysis is an opt-in research factor in the smile/cry backtest at `#backtest/<code>`. Old `#wyckoff/<code>` bookmarks lead to that asset’s backtest. The factor is off by default and uses the selected backtest dates; the existing signal formula and execution remain available without it.

## Method and interpretation

Reference: [Wyckoff Analytics — Wyckoff Method](https://www.wyckoffanalytics.com/wyckoff-method/), consulted 2026-11-05. Supply/demand, effort/result, Springs, tests, SOS and LPS inform these research proxies. Numeric thresholds below are our explicit heuristics, not canonical Wyckoff rules or calibrated trading recommendations. P&F cause/effect counts, target prices, complete A–E phase classification, SC/BC and institutional identity are not inferred.

- The primary range uses the preceding 40 closed bars by default. A 20-bar local level can qualify with two touches at least three bars apart, within the greater of 0.35× average true range or 0.2% of the level. The first candidate can occur on bar 21. The chart overlay shows the primary range when available and the 20-bar range during warm-up.
- Relative volume, high–low spread and average true range use the preceding 20 closed bars. The candidate and volume baseline require positive usable volume. Breaks must exceed the greater of 0.15% of the level or 0.12× average true range.
- Support absorption combines repeated support, a low within the support zone, a lower wick covering at least 25% of the candle, and a recovering close in the upper 35%. Volume must be at least 1.05× the baseline and spread at most 1.25×.
- Spring and UT/UTAD candidates pierce support or resistance by more than the break threshold and at most 1.5× average true range, then close inside the range in the top or bottom 40%. The range width must be within the greater of 35% of support or 12× average true range.
- SOS and SOW require a directional close beyond resistance or support and in the top or bottom 35% of the candle. Volume and spread must reach 1.25× and 1.05× respectively, or 1.10× and 1.45× respectively.
- A Spring test looks back 10 bars for a Spring and checks for a higher low near support and a narrower, lower-volume test. LPS looks back 18 bars for SOS and requires a pullback to the prior resistance area that closes at or above the level. Both use volume below 0.9×, a narrower spread than the 20-bar mean, and an upper-half close. LPS volume must also be below 80% of the SOS volume. Confirmation requires the parent event to have validated by that close.
- The separate trend context uses MA20, MA60 and the five-bar slope of MA60. The slope needs 65 bars. Up-day/down-day volume ratio uses the last 20 closed days classified by close versus prior close, excluding flat days.

Candidates are dated at the candidate close. Their status uses the **next three closed bars available as of the chosen date**. SOS and SOW validate beyond the candidate close in the breakout direction and can fail on a close back through the breakout zone. Other candidates validate after moving 0.2× average true range beyond the candidate close, capped at the candidate high or low. Their invalidation uses the candidate low or high. First validation or failure wins. The research-signals tab lists every detected shape in the selected interval with the actual level, relative volume, spread, trigger and status. This evidence is available with the factor disabled.

## Data and failure states

Inputs are existing provider OHLCV and per-bar completion flags. When the source uses record-level completion, the existing confirmed-history accessor resolves it before analysis. Intraday bars remain visible but are excluded from every structural calculation and confirmation; their raw volume is not compared to full days. The analysis cutoff is applied before any calculation. Invalid OHLC/date ordering blocks interpretation rather than silently repairing it. Missing volume, short history, unknown units/adjustments and provider failures are explicit. Unadjusted corporate actions, suspension and price limits may produce false events; the module does not infer adjusted history or synthesize missing trading days. Synthetic mode is labelled separately. There is no new polling or provider request, and an old snapshot is not described as today's market.

## Integration with smile/cry signals

- Only validated events inform the factor. The activation date is the validation close, never the candidate date. The validation close must have usable relative volume to change a signal that day.
- A same-day bullish validation adds a B smile if there is no original signal. A same-day bearish validation adds a strong cry when there is no original sell, replacing an opposite smile if present.
- Same-direction signals in the validation session and following two closed sessions are marked as supported. Within that window, an opposite smile is filtered by bearish evidence. A newer validation supersedes the previous one; same-day opposing validations prioritize the bearish direction. Existing sell signals survive contrary bullish evidence. Original three-factor buy vetoes, C-grade skips and the A-share six-ETF gate still apply. Missing volume leaves the original signal unchanged.
- One effective signal per date enters the existing next-session execution and fee/cycle accounting. The chart and signal details use that same plan; original provisional markers are displayed without Wyckoff adjustment. Signals on the final day do not trade outside the interval.
- A blue triangle stacks beneath each supported smile/cry marker, including the factor's new signals. The separate explanation table shows signal date, candidate and validation dates, volumes, spread, price test, effect on the strategy and the actual simulated order status. Its date buttons focus the existing chart.

Tests cover causal windows, event chains, missing/zero volume, validation/failure/expiry, provisional exclusion, cutoff replay, market gates, next-session execution, exit preservation, and marker construction. Numeric rules are research hypotheses rather than claims of improved future returns.
