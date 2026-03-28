# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice

---

## [LRN-20260328-001] insight

**Logged**: 2026-03-28T15:42:00Z
**Priority**: high
**Status**: promoted
**Area**: config

### Summary
Position size relative to wallet balance is the strongest signal indicator for whale alerts

### Details
WIFCAT: 195 SOL wallet bought 21.72 SOL (11%) → WIN +98% at 5m
ORCA/AGENTS: 1055 SOL wallets buying 3 SOL (0.3%) → losers/neutral
Dust buys where whales throw pocket change are noise, not signal

### Suggested Action
Filter alerts to minimum 1% position-to-wallet ratio. Already implemented in config.yaml as min_position_pct: 0.01

### Metadata
- Source: data_analysis
- Related Files: whale-tracker/config.yaml, whale-tracker/tracker.py
- Tags: whale-tracker, signal-filtering, meme-coins
- Pattern-Key: signal.position_size_filtering

---

## [LRN-20260328-002] insight

**Logged**: 2026-03-28T15:42:00Z
**Priority**: high
**Status**: promoted
**Area**: config

### Summary
Multiple whales buying the same token is a signal, not noise — do NOT deduplicate by token

### Details
Initially added 30-minute token dedup window. Walt correctly pointed out that multiple whales accumulating the same token is bullish signal. Same-token alerts from different messages should be tracked separately. Message-level dedup (same alert scraped twice) is still needed via seen_messages.json.

### Suggested Action
Token dedup disabled (dedup_window_minutes: 0). Only dedup by message_id.

### Metadata
- Source: user_feedback
- Related Files: whale-tracker/config.yaml, whale-tracker/tracker.py
- Tags: whale-tracker, deduplication, signal
- See Also: LRN-20260328-001

---

## [LRN-20260328-003] insight

**Logged**: 2026-03-228T15:42:00Z
**Priority**: medium
**Status**: promoted
**Area**: config

### Summary
Meme coins spike fast and bleed slow — quick exits are critical

### Details
WIFCAT was +300% in 6h, then pulled back -27.9% in one hour. Entry MC $70.7K → current $70.4K (flat after full round trip). Any strategy needs take-profit at +50-100% and stop-loss at -20%.

### Suggested Action
If auto-trading ever implemented, use tight take-profit/stop-loss. Don't hold meme coins.

### Metadata
- Source: data_analysis
- Related Files: whale-tracker/
- Tags: whale-tracker, exit-strategy, meme-coins

---

## [LRN-20260328-004] insight

**Logged**: 2026-03-28T15:42:00Z
**Priority**: medium
**Status**: pending
**Area**: config

### Summary
pump.fun callouts/comments are 95% noise — not useful for signal analysis

### Details
Token page comments are almost entirely rug jokes and memes. Cannot extract meaningful sentiment. Live streams slightly more useful for gauging creator engagement.

### Suggested Action
Do not attempt to parse or analyze pump.fun callouts for sentiment.

### Metadata
- Source: observation
- Tags: pump.fun, sentiment-analysis, noise

---

## [LRN-20260328-005] best_practice

**Logged**: 2026-03-28T15:42:00Z
**Priority**: medium
**Status**: promoted
**Area**: config

### Summary
Log ALL alerts (filtered or not) for later pattern analysis

### Details
Even alerts that fail filters are valuable data. Created all_alerts table that logs every alert with filter_reason. Can later analyze what got filtered vs what passed to improve filters.

### Suggested Action
Always maintain an unfiltered log alongside filtered results in data collection systems.

### Metadata
- Source: design_decision
- Related Files: whale-tracker/db.py, whale-tracker/tracker.py
- Tags: data-collection, pattern-analysis
- Pattern-Key: data.log_all_filter_results
