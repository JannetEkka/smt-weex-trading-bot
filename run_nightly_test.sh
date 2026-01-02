#!/bin/bash
echo "SMT Nightly Trade - Manual Test"
export GOOGLE_CLOUD_PROJECT=smt-weex-2025
export GOOGLE_CLOUD_LOCATION=us-central1
export GOOGLE_GENAI_USE_VERTEXAI=True
export WEEX_API_KEY=weex_cda1971e60e00a1f6ce7393c1fa2cf86
export WEEX_API_SECRET=15068d295eb937704e13b07f75f34ce30b6e279ec1e19bff44558915ef0d931c
export WEEX_API_PASSPHRASE=weex8282888
export ETHERSCAN_API_KEY=W7GTUDUM9BMBQPJUZXXMDBJH4JDPUQS9UR
pip install -q requests google-genai
python3 smt_nightly_trade.py
