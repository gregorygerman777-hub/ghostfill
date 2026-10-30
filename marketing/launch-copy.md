# Ghostfill launch copy (draft)

Status: draft, not posted. Nothing goes out without your explicit "post it".
Images: `dashboard-readiness.jpg` (top of the dashboard), `dashboard-watchlist.jpg` (charts).
Retake both without the mouse cursor before posting.

Tone: loud, confident, a little smug about the hype bots. Every bold line below is
true and checkable in the repo. That is the whole pitch.

---

## Taglines

Pick one as the headline everywhere.

1. **Every bot shows you its wins. Ghostfill shows you the truth.**
2. **Test your bot against the real market before it touches a cent.**
3. **The trading terminal that has to earn your money first.**
4. **Real Solana data. Ghost fills. Zero risk.**
5. **No screenshots of a phone. Just receipts.**

Short bio / repo description (under 160 characters):

> Ghostfill: a Solana trading terminal that paper-trades your strategy on live mainnet data, rug-checks every token, and has to prove itself before you risk a cent.

---

## X launch thread

**1/**
Everyone on here has a bot that "flipped $2 into $200k."

Nobody shows the losing trades, the fees, or the tokens that rugged.

So I built the opposite. Meet Ghostfill. 👻
[image: dashboard-readiness.jpg]

**2/**
Ghostfill runs your strategy against LIVE Solana mainnet data, 24/7.

Real prices. Real liquidity. Real pool fees, priority fees and slippage.

The only fake part is the fill. That's why it's called Ghostfill.

**3/**
Before it touches any token, it runs a 7-point rug check:

mint authority, freeze authority, risky Token-2022 extensions, top-holder concentration, liquidity, volume, pool age.

Fail one, and it doesn't trade. Every rejection is logged with the reason.

**4/**
Today it scanned JUP, BONK, WIF and RAY.

Only BONK passed. JUP's top 10 holders own 65%. RAY's own 78%. WIF's pool is too thin.

Most bots would have bought all four.
[image: dashboard-watchlist.jpg]

**5/**
Every decision streams to a live activity log:

[SCAN] [PASS] [SIGNAL] [SKIP] [FILL] [EXIT]

No black box. You see exactly why it bought, why it didn't, and what it cost.

**6/**
And here's the part nobody else ships: the Live Readiness panel.

30 days of paper trading. 50 closed trades. Profit after ALL costs. Drawdown under the limit. A walk-forward backtest that doesn't fall apart out of sample.

Until it hits all 5, it says NOT READY. In red.

**7/**
Right now my own strategy is at 1/5.

The backtest says it doesn't hold up out of sample, and Ghostfill is telling me so in red.

That's the feature. It's the bot that won't lie to you, not even for its own creator.

**8/**
Built in. No wallet. No keys. It cannot sign or send a transaction (a test scans the entire codebase to make sure).

Docker, one command, dashboard at localhost. Chrome extension with your P&L on the toolbar badge.

Open source: [GitHub link]

**9/**
If your bot is really printing, prove it in Ghostfill for 30 days.

If it isn't, you just saved yourself the tuition. 👻

---

## Standalone posts

Use between thread days.

> My trading bot just told me it's NOT READY to trade real money.
> 1 out of 5 gates. In red.
> Best feature I've ever built. 👻 [image]

> Your bot: "+$211k 🚀"
> Ghostfill: "top 10 holders own 78%. blocked."

> Hot take: if your trading bot has never shown you a losing trade, it's not a bot, it's an ad.

> Ghostfill ran a 7-point rug check on 4 of the biggest Solana tokens today.
> 1 passed.
> Imagine what it says about the coin your group chat is shilling.

> 30-day challenge: I'm running Ghostfill live on mainnet data in paper mode for a month and posting the readiness score every week. Wins, losses, fees, all of it.

The last one is the strongest growth play: a public weekly scoreboard for the month you are testing anyway.

---

## Replies under hype-bot posts

Short, not hostile, always with the screenshot.

> cool. what's the drawdown? run it through Ghostfill for 30 days and post the readiness score 👻

> show the losing trades too. that's the part that matters.

> what did it pay in fees and slippage on those fills?

---

## GitHub README hero (top of README.md)

```
# Ghostfill 👻

Every bot shows you its wins. Ghostfill shows you the truth.

Ghostfill paper-trades Solana strategies on live mainnet data: real prices, real
liquidity, real fees and slippage. Only the fill is a ghost. It rug-checks every
token, logs every decision, and keeps a Live Readiness score your strategy has to
earn before you risk a cent.

- 7-point rug check on every token, every rejection logged with its reason
- Live activity log: [SCAN] [PASS] [SIGNAL] [SKIP] [FILL] [EXIT]
- Backtests with walk-forward validation, the same cost model as live paper trading
- Live Readiness gates: 30 days, 50 trades, profit after costs, drawdown limit, out-of-sample check
- No wallet, no keys, cannot sign or send a transaction, verified by a test
- Docker in one command, dashboard plus Chrome extension
```

---

## Landing page

**Hero:** Every bot shows you its wins. Ghostfill shows you the truth.
**Sub:** Paper-trade your Solana strategy on live mainnet data until it proves it deserves real money.
**Button:** Get Ghostfill (GitHub)

**Section 1, Real market, ghost fills.** Live mainnet prices and liquidity every 30 seconds.
Every simulated fill pays pool fees, platform fees, priority fees and size-based slippage,
and some transactions fail, just like on chain.

**Section 2, Rug check before every trade.** Seven checks. One fail and the token is blocked,
with the reason on screen.

**Section 3, See every decision.** The activity log streams every scan, signal, skip and fill.

**Section 4, Earn it.** The Live Readiness panel stays red until your strategy clears all five
gates on its own paper record.

**Section 5, Can't touch your wallet.** No keys, no signing, no transactions. A test scans the
entire codebase to keep it that way.

**Footer line (keep this):** Ghostfill is a simulator. It does not trade real funds, and past
or simulated results do not predict future returns.

---

## Reddit / Hacker News post

**Title:** I got tired of "$2 to $200k" trading-bot videos, so I built a Solana bot that has to prove itself first

**Body:**
Ghostfill paper-trades strategies on live Solana mainnet data with a realistic cost model
(pool, platform and priority fees, size-based slippage, random transaction failures). It
rug-checks every token before entry, logs every decision, and keeps a Live Readiness score:
30 days, 50 trades, profit after costs, a drawdown limit, and a walk-forward backtest.

My own moving-average strategy is currently 1/5: the walk-forward test says it doesn't hold
up out of sample. I'm leaving it running for 30 days and will post the result either way.

No wallet or keys; it cannot sign or send transactions (there's a test that scans the source
for it). Python, FastAPI, Docker, plus a Chrome extension. Feedback welcome, especially on
the cost model.

---

## Lines that are off limits

These would be false today, and promising returns is the kind of claim that gets accounts banned
and draws FTC and SEC attention:

- "best / #1 / most profitable trading bot"
- any profit figure, "passive income", "prints", "guaranteed", "can't lose"
- "AI trading bot" (nothing in it is AI)
- screenshots of simulated balances presented as real money

Once a strategy really clears the 30-day gates, post that record as it is (paper results,
with the dates). That is a stronger claim than any of the above, because it is checkable.

---

## Before posting

- [ ] Put the repo on GitHub (public) and add a license
- [ ] Add the README hero above
- [ ] Retake both screenshots without the cursor, plus one of the activity log
- [ ] Record a 15 second screen capture: activity log scrolling, rug check blocking a token
- [ ] Start the weekly readiness scoreboard posts
