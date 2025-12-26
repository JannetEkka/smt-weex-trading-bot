"""
SMT Pipeline Test (NO TRADING)
Tests: Whale TX -> Signal -> Gemini Validation
"""

import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test whale transaction
TEST_WHALE_TX = {
    "whale_address": "0x28c6c06298d514db089934071355e5743bf21d60",
    "classification": "CEX_Wallet",
    "classification_confidence": 0.89,
    "tx_type": "outgoing",
    "value_eth": 500.0,
    "token": "ETH",
    "counterparty": "0x742d35cc6634c0532925a3b844bc9e7595f0ab3d"
}

print("=" * 60)
print("SMT PIPELINE TEST (NO TRADING)")
print("=" * 60)

# Step 1: Signal Creation
print("\n[1] Creating Signal from Whale TX...")
try:
    from src.signal_validator import create_trading_signal
    signal = create_trading_signal(**TEST_WHALE_TX)
    print(f"    OK: {json.dumps(signal, indent=4)}")
    step1 = True
except Exception as e:
    print(f"    FAIL: {e}")
    step1 = False

# Step 2: Gemini Validation
print("\n[2] Testing Gemini Validator...")
try:
    from src.signal_validator import GeminiSignalValidator
    validator = GeminiSignalValidator()
    print("    Validator initialized")
    
    print("    Calling Gemini with grounding...")
    result = validator.validate_signal(**signal)
    print(f"    OK: {json.dumps(result, indent=4)}")
    step2 = True
except Exception as e:
    print(f"    FAIL: {e}")
    step2 = False

# Summary
print("\n" + "=" * 60)
print("RESULTS")
print("=" * 60)
print(f"Signal Creation: {'PASS' if step1 else 'FAIL'}")
print(f"Gemini Validation: {'PASS' if step2 else 'FAIL'}")

if step1 and step2:
    print("\nPipeline ready! Run: python3 src/test_trade.py")
elif step1 and not step2:
    print("\nGemini not working. Fix auth or use fallback mode.")
