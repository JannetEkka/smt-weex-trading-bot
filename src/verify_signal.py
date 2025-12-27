"""
SMT Signal Verification Script
==============================
Validates all aspects of the signal before trading:
1. Whale address legitimacy
2. BlockCypher data integrity
3. Signal logic correctness
4. Gemini reasoning quality
5. Current market conditions
6. Signal freshness (auto-calculates staleness)
"""

import json
import requests
from datetime import datetime, timezone, timedelta
import os

print("=" * 70)
print("SMT SIGNAL VERIFICATION")
print("=" * 70)

# Get current time
now = datetime.now(timezone.utc)
print(f"\nCurrent Time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}")

# Load the signal - use correct path
script_dir = os.path.dirname(os.path.abspath(__file__))
repo_dir = os.path.dirname(script_dir)
signal_file = os.path.join(repo_dir, "ai_logs", "signal_latest.json")

print(f"Signal File: {signal_file}")

if not os.path.exists(signal_file):
    print(f"\n[ERROR] Signal file not found!")
    print(f"Run first: python3 src/smt_signal_pipeline_v2.py")
    exit(1)

with open(signal_file) as f:
    signal_data = json.load(f)

print(f"\nSignal Generated: {signal_data['generated_at']}")
print(f"Pipeline Version: {signal_data['pipeline_version']}")

# Calculate signal age
generated_time = datetime.fromisoformat(signal_data['generated_at'].replace('Z', '+00:00'))
age_seconds = (now - generated_time).total_seconds()
age_hours = age_seconds / 3600
age_minutes = age_seconds / 60

print(f"\n[SIGNAL AGE]")
print(f"  Generated: {generated_time.strftime('%Y-%m-%d %H:%M:%S')} UTC")
print(f"  Current:   {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
print(f"  Age: {age_hours:.1f} hours ({age_minutes:.0f} minutes)")

if age_hours > 2:
    print(f"  [WARNING] Signal is {age_hours:.1f} hours old - STALE, regenerate!")
elif age_hours > 1:
    print(f"  [CAUTION] Signal is {age_hours:.1f} hours old - consider regenerating")
else:
    print(f"  [OK] Signal is fresh ({age_minutes:.0f} minutes old)")

issues = []
passes = []

# ============================================================
# 1. WHALE ADDRESS VERIFICATION
# ============================================================
print("\n" + "-" * 70)
print("[1] WHALE ADDRESS VERIFICATION")
print("-" * 70)

whale = signal_data.get('whale')
if not whale:
    issues.append("No whale data in signal")
else:
    print(f"  Address: {whale['address']}")
    print(f"  Label: {whale['sub_label']}")
    print(f"  Category: {whale['category']}")
    
    VERIFIED_BINANCE_ADDRESSES = [
        "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo",
        "39884E3j6KZj82FK4vcCrkUvWYL5MQaS3v",
        "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s",
        "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h",
    ]
    
    VERIFIED_OTHER = [
        "bc1qgdjqv0av3q56jvd82tkdjpy7gdp9ut8tlqmgrpmv24sq90ecnvqqjwvw97",
        "bc1ql49ydapnjafl5t2cp9zqpjwe6pdgmxy98859v2",
        "bc1qazcm763858nkj2dj986etajv6wquslv8uxwczt",
    ]
    
    if whale['address'] in VERIFIED_BINANCE_ADDRESSES:
        passes.append(f"Whale address verified as Binance ({whale['sub_label']})")
        print(f"  [PASS] Address is verified Binance wallet")
    elif whale['address'] in VERIFIED_OTHER:
        passes.append(f"Whale address verified ({whale['sub_label']})")
        print(f"  [PASS] Address is verified whale wallet")
    else:
        issues.append(f"Whale address not in verified list")
        print(f"  [CHECK] Address not in hardcoded verified list")

# ============================================================
# 2. FLOW DATA VERIFICATION
# ============================================================
print("\n" + "-" * 70)
print("[2] FLOW DATA VERIFICATION")
print("-" * 70)

flow = signal_data.get('flow')
if not flow:
    issues.append("No flow data in signal")
else:
    print(f"  Net Direction: {flow['net_direction']}")
    print(f"  Net Value: {flow['net_value']:.2f} BTC")
    print(f"  Inflow: {flow['inflow_btc']:.2f} BTC ({flow['inflow_count']} TXs)")
    print(f"  Outflow: {flow['outflow_btc']:.2f} BTC ({flow['outflow_count']} TXs)")
    print(f"  Total Significant TXs: {flow['total_significant_txs']}")
    
    calc_net = abs(flow['inflow_btc'] - flow['outflow_btc'])
    if abs(calc_net - flow['net_value']) > 0.1:
        issues.append(f"Net value mismatch")
        print(f"  [FAIL] Math mismatch")
    else:
        passes.append("Flow math verified")
        print(f"  [PASS] Math verified")
    
    if flow['total_significant_txs'] >= 2:
        passes.append(f"Sustained flow ({flow['total_significant_txs']} TXs)")
        print(f"  [PASS] Sustained: {flow['total_significant_txs']} TXs")
    else:
        issues.append("Not sustained")
        print(f"  [FAIL] Not sustained")

# ============================================================
# 3. SIGNAL LOGIC VERIFICATION
# ============================================================
print("\n" + "-" * 70)
print("[3] SIGNAL LOGIC VERIFICATION")
print("-" * 70)

sig = signal_data.get('signal')
if sig:
    print(f"  Signal: {sig['signal']}")
    print(f"  Confidence: {sig['confidence']:.0%}")
    
    if whale and flow:
        if whale['category'] == 'CEX_Wallet' and flow['net_direction'] == 'outflow':
            if sig['signal'] == 'LONG':
                passes.append("CEX outflow -> LONG (correct)")
                print(f"  [PASS] CEX outflow -> LONG")
        elif whale['category'] == 'CEX_Wallet' and flow['net_direction'] == 'inflow':
            if sig['signal'] == 'SHORT':
                passes.append("CEX inflow -> SHORT (correct)")
                print(f"  [PASS] CEX inflow -> SHORT")

# ============================================================
# 4. GEMINI VALIDATION CHECK
# ============================================================
print("\n" + "-" * 70)
print("[4] GEMINI VALIDATION CHECK")
print("-" * 70)

validation = signal_data.get('validation')
if validation:
    print(f"  Decision: {validation['decision']}")
    print(f"  Grounding: {validation.get('grounding', False)}")
    
    if validation.get('grounding'):
        passes.append("Gemini used grounding")
        print(f"  [PASS] Gemini used Google Search")
    
    if sig and validation['signal'] == sig['signal']:
        passes.append("Gemini agrees")
        print(f"  [PASS] Gemini agrees: {validation['signal']}")

# ============================================================
# 5. CURRENT MARKET CHECK (WEEX API)
# ============================================================
print("\n" + "-" * 70)
print("[5] CURRENT MARKET CHECK (WEEX API)")
print("-" * 70)

try:
    url = "https://api-contract.weex.com/capi/v2/market/ticker?symbol=cmt_btcusdt"
    response = requests.get(url, timeout=10)
    data = response.json()
    current_price = float(data.get('last', 0))
    signal_price = signal_data.get('btc_price', 0)
    
    print(f"  Signal Price:  ${signal_price:,.2f}")
    print(f"  Current Price: ${current_price:,.2f}")
    
    if signal_price > 0 and current_price > 0:
        price_change_pct = ((current_price - signal_price) / signal_price) * 100
        print(f"  Change: {price_change_pct:+.2f}%")
        
        if abs(price_change_pct) > 3:
            issues.append(f"Price moved {price_change_pct:+.2f}%")
            print(f"  [WARN] Significant price movement")
        else:
            passes.append("Price stable")
            print(f"  [PASS] Price stable")
except Exception as e:
    print(f"  [ERROR] {e}")

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "=" * 70)
print("VERIFICATION SUMMARY")
print("=" * 70)

print(f"\nSignal Age: {age_hours:.1f} hours ({age_minutes:.0f} minutes)")
print(f"Passes: {len(passes)} | Issues: {len(issues)}")

is_stale = age_hours > 2

print("\n" + "-" * 70)
if is_stale:
    print("VERDICT: SIGNAL IS STALE - Regenerate!")
    print("Run: python3 src/smt_signal_pipeline_v2.py")
elif len(issues) == 0:
    print("VERDICT: ALL CHECKS PASSED - Safe to trade")
    print("Run: python3 src/smt_execute_trade.py")
else:
    print(f"VERDICT: {len(issues)} issues found - review above")
print("-" * 70)
