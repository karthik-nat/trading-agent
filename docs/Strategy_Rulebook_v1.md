# Strategy Rulebook — Multi-Stock Technical Swing System (v1)

**Owner:** [you]
**Version:** 1.0 (trend-pullback engine)
**Status:** Draft for review and tuning
**Last updated:** 2026-06-17

---

## 0. How to read this document

This is the **single source of truth** for the strategy. Every threshold below is a **tunable parameter** with a stated default; the consolidated list lives in §12. The build plan and code will implement *these exact rules*, so this document — not the code — is where strategy decisions are made and changed. When you want to change behavior, change a number here first, then re-test, then update the code.

Nothing here is investment advice. This is a personal decision-support framework. The system **recommends**; you review every recommendation and place every order manually.

---

## 1. Purpose, scope, and honest framing

**What this is.** An experiment to measure how a rules-based, technically-driven swing-trading algorithm performs on a portfolio of individual US stocks. The intent is to generate enough trades, logged cleanly, to compute a real edge estimate (expectancy) and compare entry engines.

**Capital.** Start ~$5,000 (explicitly risk capital — loss of up to 50% accepted). Potential scale to ~$50,000 after several months *only if validated metrics justify it*.

**Instruments.** 100% individual US common stocks. **No** ETFs, mutual funds, options, or crypto in this version. (Indices may be referenced as *indicators* — see §3 — but never held.)

**Style.** Long-only, trend-aligned swing trades held **weeks to months**. Execution is manual in a retail brokerage (Robinhood / Merrill); the system never trades on its own.

**Why the risk rules are strict.** The base rates for active individual trading are poor: in the largest US study, the most active 20% of traders underperformed the market by roughly **6.5 percentage points per year** after costs (Barber & Odean, 2000), and in the Taiwan data **more than 80% of day traders lost money** after costs in a typical six-month window (Barber, Lee, Liu & Odean). The risk layer in §7–§9 exists so the experiment survives long enough to produce statistically meaningful data rather than ending in an early drawdown.

---

## 2. Strategy summary (one paragraph)

Hold a diversified book of **8–12 individual stocks** selected by a **trend-pullback** technical engine: only buy stocks in confirmed uptrends, on controlled pullbacks toward a rising moving average, when RSI, MACD, and volume confirm a resumption of the trend. Size every position so a stop-out costs **≤1% of the account**. Exit on a pre-defined ATR/structure stop, scale out at a profit multiple, trail the rest under a moving average, and cut dead trades on a time stop. Enforce per-name, sector, and correlation caps so no single bet or theme can dominate, and halt new buys on a portfolio drawdown circuit breaker.

---

## 3. Universe & tradeability filters (the gate)

A stock is **eligible** only if it passes ALL of the following. These are hard pass/fail gates, not scored.

| Filter | Default | Rationale |
|---|---|---|
| Listing | US primary-listed common stock (NYSE / Nasdaq) | Avoid OTC/pink-sheet illiquidity |
| Min price | ≥ $10/share | Avoid penny-stock noise and wide spreads |
| Min avg dollar volume | ≥ $20M/day (20-day avg) | Ensures you can enter/exit without moving price |
| Min market cap | ≥ $2B | Liquidity + lower manipulation/gap risk |
| Earnings blackout | **Exclude if earnings report falls within the expected hold window** (default: no entry within 10 calendar days before earnings) | Earnings gaps jump straight through stops |
| Corporate-action exclusion | Exclude pending M&A / halted / known delisting | Technicals are meaningless on these |
| Data quality | Must have ≥ 250 trading days of clean history | Needed to compute 200-day MA and indicators |

**Output of this stage:** a daily "tradeable universe" (typically a few hundred names).

---

## 4. Regime filter (trend gate)

Only **long** setups are considered, and only in uptrends. A name passes the regime gate when ALL hold:

| Condition | Default | Meaning |
|---|---|---|
| Price > 200-day SMA | required | Long-term uptrend |
| 50-day SMA > 200-day SMA | required | Intermediate trend aligned ("golden cross" regime) |
| 200-day SMA slope | rising over last 20 days | Trend is up, not flat/rolling over |
| **Market filter** | S&P 500 (SPX) > its own 200-day SMA | Risk-on switch — no *new* longs when the broad market is below its 200-day |

The market filter uses the index purely as an indicator; you never buy it. When the market filter is OFF (SPX below 200-day), the system manages existing positions but **issues no new buy signals**.

---

## 5. Entry engine — TREND-PULLBACK (primary)

Among names passing §3 and §4, a **buy candidate** is generated when the pullback-and-resume pattern is confirmed. Use **confluence**: the trend gate is already satisfied; now require a pullback, a momentum turn, and confirmation. Default rule = **all four** conditions below true on the signal day (a stricter "≥3 of 4" variant is noted in §12 for tuning).

| # | Condition | Default | Notes |
|---|---|---|---|
| E1 | **Pullback to MA** — price has pulled back to within X% of the rising 20-day or 50-day SMA, or tagged it intraday in the last 1–3 days | within 2% of 20-SMA (or touched 50-SMA) | Buying a dip in an uptrend, not chasing extension |
| E2 | **RSI turn** — RSI(14) dipped into the 40–50 zone during the pullback and has **turned back up** (today's RSI > yesterday's) | RSI(14) crossed back above 45–50 from below | Trend-following use of RSI, *not* the <30 oversold rule |
| E3 | **MACD resume** — MACD(12,26,9) line crosses above its signal line, or histogram turns from negative to positive | bullish cross or histogram up-tick | Prefer when MACD line is above zero |
| E4 | **Volume confirm** — resumption day volume ≥ 1.2× the 20-day average volume | ≥ 1.2× avg | Pullback ideally on *below*-average volume (healthy) |

**Anti-chase guard:** reject the signal if price is already extended > 8% above the 20-day SMA (you missed the entry; don't chase).

**Ranking when more candidates than open slots exist:** rank eligible buy candidates by a composite of (a) relative strength vs. SPX over 3 months (higher = better) and (b) proximity of entry to the protective stop (tighter stop = better reward/risk). Fill open slots top-down, subject to the portfolio caps in §6.

---

## 6. Portfolio construction & diversification (carries the load — no fund ballast)

Because there is no index fund underneath the book, these caps **are** the diversification system.

| Rule | Default | Purpose |
|---|---|---|
| Target positions held | 8–12 | Enough that no single name is fatal; few enough to manage |
| Min positions when fully deployed | ≥ 6 | Below this, don't be "fully invested" — hold cash instead |
| Max weight per single name | ≤ 10% of account at cost | Caps single-name blowup damage |
| Max sector weight | ≤ 30% of account | **The real diversification substitute** — prevents a secretly-one-bet book |
| Correlation guard | Flag if a new buy has > 0.7 trailing 60-day return correlation with an existing holding; limit to ≤ 2 highly-correlated names | 10 correlated stocks diversify almost nothing |
| Cash floor | Keep ≥ 5% cash buffer | Avoids forced selling; dry powder |
| New positions per day | ≤ 2 | Prevents over-deploying on a single signal-rich day |

---

## 7. Position sizing (the survival math)

Size is **computed from risk, never guessed.** For each candidate:

```
risk_dollars   = account_equity × risk_per_trade_pct
stop_distance  = entry_price − initial_stop_price
shares         = floor( risk_dollars / stop_distance )
position_value = shares × entry_price
```

| Parameter | Default | Notes |
|---|---|---|
| `risk_per_trade_pct` | **1.0%** (start at 0.5–1.0%) | Max loss if stopped at initial stop |
| Hard cap on `position_value` | ≤ 10% of account (§6) | If risk-sizing implies a bigger position, the §6 cap wins |
| Min position size | skip if shares < a tradeable amount or position_value < ~$200 | Avoid dust positions (relevant at $5K) |

**Worked example at $5,000, 1% risk:** risk_dollars = $50. If entry = $100 and initial stop = $95 (stop_distance $5), shares = floor(50/5) = 10, position_value = $1,000 (20% of account → **exceeds the 10% cap**, so reduce to 5 shares / $500). At $5K, expect to hold fewer names than the 8–12 target until the account grows; fractional shares (if your broker supports them) help here.

---

## 8. Exit rules (where swing trading is won or lost)

Every trade is defined in **R-multiples**, where **1R = entry_price − initial_stop_price** (the dollar risk per share). All exits are set in the plan *before* entry.

| Exit | Default | Logic |
|---|---|---|
| **Initial stop** | max(1.5 × ATR(14) below entry, recent swing low) — whichever gives the *tighter* sensible stop | Primary risk control. Never widen a stop after entry. |
| **First profit scale** | Sell ½ position at **+2R** | Locks in a winner, reduces risk to near-zero on the runner |
| **Trail the remainder** | Trailing stop = max(close below 20-day SMA, 2.5 × ATR trailing) | Lets winners run while protecting gains |
| **Trend-break exit** | Exit remainder on close below 50-day SMA **or** bearish MACD cross with price rolling over | Trend is over |
| **Time stop** | Exit if trade hasn't reached +1R within **20 trading days** | Dead money = opportunity cost; thesis failed |
| **Hard event exit** | Exit before a scheduled earnings date if still held (per §3 you won't enter near earnings, but a long hold can approach one) | Avoid binary gap risk |

**Move-to-breakeven rule:** once a trade reaches +1R unrealized, raise the stop to breakeven (entry price).

---

## 9. Portfolio-level risk controls (circuit breakers)

These override everything else.

| Control | Default | Action when triggered |
|---|---|---|
| **Drawdown circuit breaker** | Account down ≥ 15% from peak equity | Stop all new buys; manage existing only; require manual review before re-enabling |
| **Hard drawdown halt** | Account down ≥ 25% from peak | Halt the experiment; full review of whether the edge is real |
| **Consecutive-loss cooling-off** | 4 stop-outs in a row | Pause new entries for 3 trading days; review log for a regime problem |
| **Daily new-risk cap** | Total *new* risk opened in one day ≤ 2% of account | Prevents stacking correlated new risk in one session |
| **Heat cap (total open risk)** | Sum of all open-position risk (to stops) ≤ 6% of account | Caps worst-case simultaneous-stop loss |

---

## 10. The twice-daily monitor (what each run does)

The system runs on the schedule from the build plan (morning pre-open prep; afternoon near-close). Each run it recomputes indicators on held names and the universe, then **produces recommendations only** — never orders.

**Morning run (pre-open):**
- Recompute regime + market filter; if market filter flipped OFF overnight, flag "no new buys."
- Check held positions for gap risk, hit stops, earnings approaching, trend-break or time-stop conditions → output **manage/exit recommendations**.
- Scan universe for fresh §5 buy candidates → output a **ranked candidate list** with computed entry, stop, size, and which cap (if any) constrains it.
- Surface all §9 circuit-breaker states.

**Afternoon run (near close):**
- Re-confirm any morning buy candidates still valid into the close (avoid acting on signals that faded).
- Confirm end-of-day exits (close-below-MA / MACD-cross exits are evaluated on the closing print).
- Update trailing stops and breakeven moves for tomorrow.
- Write the day's state to the journal (§11).

**Output format (per recommendation):** action (BUY / TRIM / EXIT / HOLD), ticker, reason codes (which rules fired), entry/stop/target, share count, position % and risk %, and any cap that bound the size.

---

## 11. Metrics & experiment protocol (this is the "science")

**Validate before risking real money.** Backtest the rules on history and/or paper-trade (mirror in an Alpaca paper account) until you have a meaningful sample, *then* deploy the $5K. Go-live gate: positive expectancy after modeled costs and slippage over **≥ 50 trades**.

**Log every trade** with: entry/exit dates and prices, reason codes, R-multiple result, holding period, sector, and market-regime state at entry.

**Track these metrics** (per the analytics libraries in the build plan):

| Metric | What it tells you | Watch-for |
|---|---|---|
| Expectancy (avg R per trade) | The core edge number | Must be > 0 after costs |
| Win rate | % of trades profitable | Trend systems often win < 50% but win big |
| Avg win ÷ avg loss (payoff ratio) | Asymmetry | Want > 1.5 for a trend system |
| Max drawdown | Worst peak-to-trough | Compare to your 15%/25% breakers |
| Sharpe / Sortino | Risk-adjusted return | Context, not a single verdict |
| % trades stopped at initial stop | Stop discipline / placement | Very high → stops too tight |
| Slippage vs. modeled | Real-world execution drag | Manual fills differ from backtest |

---

## 12. Consolidated parameter table (tune here)

| Param | Default | Group |
|---|---|---|
| min_price | $10 | Universe |
| min_avg_dollar_volume | $20M | Universe |
| min_market_cap | $2B | Universe |
| earnings_blackout_days | 10 | Universe |
| trend_ma_long | 200 SMA | Regime |
| trend_ma_mid | 50 SMA | Regime |
| trend_ma_short | 20 SMA | Entry |
| market_filter | SPX > 200 SMA | Regime |
| pullback_proximity | within 2% of 20-SMA | Entry E1 |
| rsi_period | 14 | Entry E2 |
| rsi_turn_zone | 40–50, turning up | Entry E2 |
| macd_params | 12 / 26 / 9 | Entry E3 |
| volume_confirm_mult | 1.2× 20-day avg | Entry E4 |
| entry_confluence | all 4 (alt: ≥3 of 4) | Entry |
| anti_chase_max_ext | 8% above 20-SMA | Entry |
| risk_per_trade_pct | 1.0% | Sizing |
| max_position_pct | 10% | Portfolio |
| max_sector_pct | 30% | Portfolio |
| correlation_flag | 0.7 (60-day) | Portfolio |
| target_positions | 8–12 | Portfolio |
| min_positions_full | 6 | Portfolio |
| cash_floor_pct | 5% | Portfolio |
| max_new_positions_per_day | 2 | Portfolio |
| atr_period | 14 | Exit |
| initial_stop | max(1.5×ATR, swing low) | Exit |
| first_scale | ½ at +2R | Exit |
| trail_rule | close < 20-SMA or 2.5×ATR | Exit |
| trend_break_exit | close < 50-SMA or bearish MACD | Exit |
| time_stop_days | 20 (need +1R) | Exit |
| breakeven_trigger | +1R | Exit |
| dd_circuit_breaker | −15% from peak | Risk |
| dd_hard_halt | −25% from peak | Risk |
| consec_loss_pause | 4 | Risk |
| daily_new_risk_cap | 2% | Risk |
| total_heat_cap | 6% | Risk |
| go_live_min_trades | 50 (expectancy > 0) | Protocol |

---

## 13. Alternate entry engines (modules for later A/B testing)

Same §3 universe, §4 regime, §6–§9 risk/portfolio scaffolding. Only the entry trigger swaps.

**Breakout engine.** Buy when price breaks above a defined consolidation high / N-week high (default: 20-day high) on volume ≥ 1.5× average, with RSI/MACD confirming momentum. Tends to higher win size, lower win rate, more whipsaw than pullback.

**Mean-reversion engine.** In still-uptrending names (price > 200-SMA), buy short-term oversold bounces — RSI(2) < 10 or RSI(14) < 30 with a reversal bar — and exit fast (RSI back above 50–60, or 3–8 day hold). Higher trade frequency, shorter holds, different (and more cost-sensitive) profile. Test only after the pullback baseline is established.

**A/B method:** run the alternate engine in paper alongside the live pullback engine, compare expectancy and drawdown on matched periods before reallocating.

---

## 14. What this is NOT (caveats)

- **Not a guaranteed edge.** Technical-indicator swing trading has mixed academic support; the discipline (sizing, exits, diversification) does much of the protective work regardless of whether the entry signals add alpha.
- **Not tax-optimized.** Weeks-to-months holds generate **short-term capital gains** (taxed as ordinary income) and frequent wash-sale situations. Factor this into net-return expectations.
- **Not set-and-forget.** It is decision-support; a human reviews and places every order.
- **Not a substitute for the survival rules.** If a conflict arises, the risk caps (§7–§9) always win over a return-seeking signal (§5).

---

## 15. Sources

- Barber, B. & Odean, T. (2000). *Trading Is Hazardous to Your Wealth: The Common Stock Investment Performance of Individual Investors.* Journal of Finance, 55(2), 773–806. — Most active quintile underperformed the market by ~6.5 percentage points/year after costs.
- Barber, B., Lee, Y., Liu, Y. & Odean, T. (Taiwan day-trading studies, Journal of Financial Markets 2014; RFS 2009). — >80% of day traders lost money after costs in a typical six-month period; only ~18% profitable in the following six months.
- Jegadeesh, N. & Titman, S. (1993). — Momentum premium (+3–5% annualized historically); basis for trend/momentum signals.
- Momentum crash magnitude (recent winners can fall 30–50% faster than the market in reversals): factor-investing literature summaries (e.g., MSCI Foundations of Factor Investing; practitioner reviews, 2025–2026).
- Long-run US equity benchmark ~10%/yr nominal (~6–7% real): Fidelity (30-yr S&P 500 ~10.4%, 1996–2025); DQYDJ S&P 500 return calculator.

*Indicator default parameters (RSI 14; MACD 12/26/9; SMAs 20/50/200; ATR 14) are standard technical-analysis conventions, included here as tunable defaults rather than as empirical claims.*

---

*End of Rulebook v1. Change a parameter here → re-test → then update the code.*
