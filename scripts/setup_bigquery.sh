#!/bin/bash
# BigQuery Setup and Data Upload Commands for SMT-WEEX
# Run these in Cloud Shell

# ============================================
# 1. SET PROJECT
# ============================================
gcloud config set project smt-weex-2025

# ============================================
# 2. CREATE DATASETS (if not exists)
# ============================================
bq mk --dataset --location=US smt-weex-2025:raw_data
bq mk --dataset --location=US smt-weex-2025:processed_data
bq mk --dataset --location=US smt-weex-2025:ml_data

# ============================================
# 3. UPLOAD WHALE FEATURES TO BIGQUERY
# ============================================
# Upload the features CSV to BigQuery
bq load \
    --source_format=CSV \
    --autodetect \
    --replace \
    ml_data.whale_features \
    data/whale_features_20251217_223129.csv

# ============================================
# 4. UPLOAD TRANSACTIONS TO BIGQUERY
# ============================================
bq load \
    --source_format=CSV \
    --autodetect \
    --allow_jagged_rows \
    --allow_quoted_newlines \
    --max_bad_records=1000 \
    --replace \
    raw_data.whale_transactions \
    data/whale_transactions_20251217.csv

# ============================================
# 5. UPLOAD FILTERED WHALES TO BIGQUERY
# ============================================
bq load \
    --source_format=CSV \
    --autodetect \
    --replace \
    processed_data.top_whales_filtered \
    data/top_whales_filtered.csv

# ============================================
# 6. UPLOAD BALANCES TO BIGQUERY
# ============================================
bq load \
    --source_format=CSV \
    --autodetect \
    --replace \
    processed_data.whale_balances \
    data/whale_balances_20251217_173852.csv

# ============================================
# 7. VERIFY UPLOADS
# ============================================
echo "=== Verifying uploads ==="
bq query --use_legacy_sql=false 'SELECT COUNT(*) as cnt FROM `smt-weex-2025.ml_data.whale_features`'
bq query --use_legacy_sql=false 'SELECT COUNT(*) as cnt FROM `smt-weex-2025.raw_data.whale_transactions`'
bq query --use_legacy_sql=false 'SELECT category, COUNT(*) as cnt FROM `smt-weex-2025.ml_data.whale_features` GROUP BY category'

# ============================================
# 8. ALSO UPLOAD TO GCS FOR COLAB ACCESS
# ============================================
gsutil cp data/whale_features_20251217_223129.csv gs://smt-weex-2025-models/data/whale_features.csv
gsutil cp data/whale_transactions_20251217.csv gs://smt-weex-2025-models/data/whale_transactions.csv
gsutil cp data/top_whales_filtered.csv gs://smt-weex-2025-models/data/top_whales_filtered.csv

echo "=== Done! ==="
echo "BigQuery tables created in smt-weex-2025"
echo "Files also uploaded to gs://smt-weex-2025-models/data/"
