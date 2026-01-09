# SMT V3 Pipeline
## WEEX AI Wars Hackathon (Jan 12 - Feb 2, 2026)

---

## Folder Structure

```
v3/
├── smt_nightly_trade_v3.py    # Main trading pipeline
├── smt_daemon_v3.py           # 24/7 daemon service
├── smt_position_monitor_v3.py # Position monitoring
├── smt                        # Control script (chmod +x)
├── smt-trading.service        # systemd service file
├── setup_daemon.sh            # One-time setup script
├── requirements-v3.txt        # Python dependencies
├── .env.example               # Environment variables template
├── test_contract_info.py      # Test WEEX contract stepSize
└── README.md                  # This file
```

---

## First Successful Trade (Jan 8, 2026)

```
Order ID: 704036529323901285
Pair: SOL
Signal: LONG
Size: 0.7 SOL (~$95 USDT)
Entry: $135.17
TP: $141.93 (+5.0%)
SL: $131.79 (-2.5%)
Leverage: 20x
```

**Potential Outcomes:**
- TP hit: +$95 profit
- SL hit: -$47.5 loss

---

## WEEX StepSize Requirements

Different pairs have different minimum step sizes:

| Pair | stepSize | min_order | Example $100 |
|------|----------|-----------|--------------|
| ETH | 0.001 | 0.001 | 0.032 |
| BTC | 0.0001 | 0.0001 | 0.0011 |
| SOL | 0.1 | 0.1 | 0.7 |
| DOGE | 1 | 100 | 696 |
| XRP | 1 | 10 | 47 |
| ADA | 1 | 10 | 253 |
| BNB | 0.1 | 0.1 | 0.1 |
| LTC | 0.1 | 0.1 | 1.2 |

The pipeline auto-fetches stepSize from WEEX API and rounds accordingly.

---

## V3 Architecture

### Pipeline Flow
```
1. Whale Discovery (Etherscan V2)
   - Fetch large transactions (>100 ETH, last 6 hours)
   - Discover whale addresses from CEX interactions
   
2. Whale Classification (CatBoost or Rule-based fallback)
   - Extract 32 features from 90-day tx history
   - Classify: CEX_Wallet, Large_Holder, DeFi_Trader, Staker, Miner
   - Generate signal based on flow direction
   
3. Multi-Pair Analysis (Gemini 2.5 Flash + Google Search)
   - Tier 1 (ETH, BTC): Whale data + Grounding
   - Tier 2 (SOL, DOGE, XRP, ADA, BNB, LTC): Grounding only
   - Competition-aware prompts with balance, TP/SL calculations
   
4. Trade Execution (WEEX API)
   - Smart position sizing (5-20% based on confidence)
   - Set TP/SL on order
   - Auto-upload AI log to WEEX
   
5. Position Monitoring (Daemon)
   - Check every 5 minutes for TP/SL hit
   - Cancel remaining orders when position closes
   - Force close after max hold time (48h default, 6h final days)
```

### Key Features
- **Dynamic position sizing**: Based on balance, confidence, competition timeline
- **Competition-aware prompts**: Includes balance, P&L, days remaining, risk/reward calculations
- **Auto order cleanup**: Cancels orphaned TP/SL orders when position closes
- **Test mode**: `--test` flag for dry runs without real trades
- **24/7 daemon**: systemd service with auto-restart

---

## Files on VM

| File | Purpose |
|------|---------|
| `smt_nightly_trade_v3.py` | Main pipeline (1663 lines) |
| `smt_daemon_v3.py` | 24/7 daemon service |
| `smt_position_monitor_v3.py` | Position monitoring |
| `smt-trading.service` | systemd service file |
| `smt` | Control script |
| `requirements-v3.txt` | Python dependencies |
| `.env` | API credentials |

---

## Commands

### Run Pipeline Once
```bash
# Test mode (no real trades)
python3 smt_nightly_trade_v3.py --test

# Live mode
python3 smt_nightly_trade_v3.py
```

### Daemon Control
```bash
./smt start      # Start 24/7 daemon
./smt stop       # Stop daemon
./smt status     # Check status
./smt logs       # View logs
./smt test       # Run in test mode
./smt positions  # Show WEEX positions
./smt cleanup    # Cancel all pending orders
```

### Manual systemd
```bash
sudo systemctl start smt-trading
sudo systemctl stop smt-trading
sudo systemctl status smt-trading
sudo systemctl enable smt-trading  # Auto-start on boot
```

---

## Configuration

### Trading Parameters
```python
MAX_LEVERAGE = 20
MAX_OPEN_POSITIONS = 5
MAX_SINGLE_POSITION_PCT = 0.25  # Max 25% of balance per trade
MAX_TOTAL_EXPOSURE_PCT = 0.60   # Max 60% total exposure

# Position sizing by confidence
Tier 1 (Whale data): 5-20% of balance
Tier 2 (Grounding only): 5-10% of balance
```

### Competition Dates
```python
COMPETITION_START = datetime(2026, 1, 12)
COMPETITION_END = datetime(2026, 2, 2)
```

### Timing (Daemon)
```python
SIGNAL_CHECK_INTERVAL = 4 hours   # Check for new trades
POSITION_MONITOR_INTERVAL = 5 min # Check positions
CLEANUP_CHECK_INTERVAL = 30 sec   # Quick cleanup check
```

---

## Known Issues & Fixes

### 1. Daemon now auto-detects practice mode
If you have balance before Jan 12, the daemon will run in practice mode automatically.
You can also force it with: `./smt test --force`

### 2. pip --break-system-packages
Old pip version doesn't support this flag.
**Fix:** Just run `pip3 install -r requirements-v3.txt` (already works)

---

## VM Details

```
Name: smt-api-test
Zone: asia-southeast1-b
IP: 34.87.116.79 (whitelisted by WEEX)
User: smtforweex
Repo: ~/smt-weex-trading-bot
```

### SSH Commands
```bash
# Start VM
gcloud compute instances start smt-api-test --zone=asia-southeast1-b

# SSH
gcloud compute ssh smt-api-test --zone=asia-southeast1-b

# Stop VM (save costs)
gcloud compute instances stop smt-api-test --zone=asia-southeast1-b
```

---

## Next Steps

1. **Update requirements on VM**:
   ```bash
   pip3 install -r requirements-v3.txt
   ```

2. **Practice trades NOW** - daemon auto-detects balance and runs

3. **Start daemon**:
   ```bash
   ./smt start
   ```

4. **Commit to repo**:
   ```bash
   git add .
   git commit -m "V3 pipeline - tested and working with practice mode"
   git push
   ```

5. **Monitor**:
   ```bash
   ./smt logs
   ./smt positions
   ```

---

## WEEX AI Log Requirement

Pipeline auto-uploads AI logs to WEEX after each decision:
- Whale Discovery
- Signal Generation
- Trade Execution
- Position Close

Endpoint: `POST /capi/v2/order/uploadAiLog`

---

## GitHub Repo
https://github.com/JannetEkka/smt-weex-trading-bot

---

*Last updated: January 7, 2026*
*Pipeline version: SMT-v3.0-Competition*
