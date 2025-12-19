"""
Whale Classifier
Loads trained CatBoost model and classifies whale wallet behavior
"""

import os
import json
import pickle
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Tuple, List, Optional
from catboost import CatBoostClassifier
from google.cloud import storage, bigquery

# Configuration
PROJECT_ID = os.getenv('GOOGLE_CLOUD_PROJECT', 'smt-weex-2025')
BUCKET_NAME = os.getenv('GCS_BUCKET', 'smt-weex-2025-models')
MODEL_PATH = 'models/production/catboost_whale_classifier_production.cbm'
ENCODER_PATH = 'models/production/label_encoder_production.pkl'
FEATURES_PATH = 'models/production/features.json'


class WhaleClassifier:
    """CatBoost-based whale behavior classifier"""
    
    def __init__(self, local_model_dir: str = None):
        """
        Initialize classifier
        
        Args:
            local_model_dir: Local directory with model files (optional)
                            If None, downloads from GCS
        """
        self.model = None
        self.label_encoder = None
        self.features = None
        self.watched_whales = {}
        self.feature_cache = {}
        
        # Load model
        if local_model_dir:
            self._load_local(local_model_dir)
        else:
            self._load_from_gcs()
        
        # Load watched whales from BigQuery
        self._load_watched_whales()
    
    def _load_from_gcs(self):
        """Download and load model from GCS"""
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        
        # Create temp directory
        os.makedirs('/tmp/smt_model', exist_ok=True)
        
        # Download model
        blob = bucket.blob(MODEL_PATH)
        local_model = '/tmp/smt_model/model.cbm'
        blob.download_to_filename(local_model)
        
        # Download label encoder
        blob = bucket.blob(ENCODER_PATH)
        local_encoder = '/tmp/smt_model/encoder.pkl'
        blob.download_to_filename(local_encoder)
        
        # Download features
        blob = bucket.blob(FEATURES_PATH)
        local_features = '/tmp/smt_model/features.json'
        blob.download_to_filename(local_features)
        
        # Load
        self._load_local('/tmp/smt_model')
    
    def _load_local(self, model_dir: str):
        """Load model from local directory"""
        # Load CatBoost model
        model_file = os.path.join(model_dir, 'model.cbm')
        if not os.path.exists(model_file):
            model_file = os.path.join(model_dir, 'catboost_whale_classifier_production.cbm')
        
        self.model = CatBoostClassifier()
        self.model.load_model(model_file)
        
        # Load label encoder
        encoder_file = os.path.join(model_dir, 'encoder.pkl')
        if not os.path.exists(encoder_file):
            encoder_file = os.path.join(model_dir, 'label_encoder_production.pkl')
        
        with open(encoder_file, 'rb') as f:
            self.label_encoder = pickle.load(f)
        
        # Load features
        features_file = os.path.join(model_dir, 'features.json')
        with open(features_file, 'r') as f:
            self.features = json.load(f)['features']
        
        print(f"Model loaded: {len(self.label_encoder.classes_)} classes, {len(self.features)} features")
        print(f"Classes: {list(self.label_encoder.classes_)}")
    
    def _load_watched_whales(self):
        """Load whale addresses and their pre-computed features from BigQuery"""
        try:
            client = bigquery.Client(project=PROJECT_ID)
            
            query = """
            SELECT address, category, sub_label, balance_eth
            FROM `smt-weex-2025.ml_data.whale_features`
            """
            
            df = client.query(query).to_dataframe()
            
            for _, row in df.iterrows():
                self.watched_whales[row['address'].lower()] = {
                    'category': row['category'],
                    'sub_label': row['sub_label'],
                    'balance_eth': row['balance_eth']
                }
            
            print(f"Loaded {len(self.watched_whales)} watched whales")
            
        except Exception as e:
            print(f"Warning: Could not load watched whales from BQ: {e}")
            self.watched_whales = {}
    
    def get_whale_features(self, address: str) -> Optional[np.ndarray]:
        """Get features for a whale address from BigQuery"""
        address = address.lower()
        
        # Check cache
        if address in self.feature_cache:
            return self.feature_cache[address]
        
        try:
            client = bigquery.Client(project=PROJECT_ID)
            
            query = f"""
            SELECT *
            FROM `smt-weex-2025.ml_data.whale_features`
            WHERE LOWER(address) = '{address}'
            """
            
            df = client.query(query).to_dataframe()
            
            if len(df) == 0:
                return None
            
            # Extract features in correct order
            feature_values = df[self.features].values[0]
            
            # Cache
            self.feature_cache[address] = feature_values
            
            return feature_values
            
        except Exception as e:
            print(f"Error fetching features for {address}: {e}")
            return None
    
    def classify(self, address: str) -> Tuple[str, float]:
        """
        Classify a whale address
        
        Args:
            address: Wallet address
        
        Returns:
            Tuple of (classification, confidence)
        """
        address = address.lower()
        
        # Check if in watched whales (known classification)
        if address in self.watched_whales:
            return self.watched_whales[address]['category'], 0.95
        
        # Get features
        features = self.get_whale_features(address)
        
        if features is None:
            return 'Unknown', 0.0
        
        # Predict
        features = features.reshape(1, -1)
        prediction = self.model.predict(features)
        probabilities = self.model.predict_proba(features)
        
        # Get class and confidence
        pred_idx = int(prediction[0])
        confidence = float(probabilities[0][pred_idx])
        category = self.label_encoder.inverse_transform([pred_idx])[0]
        
        return category, confidence
    
    def classify_batch(self, addresses: List[str]) -> List[Tuple[str, str, float]]:
        """Classify multiple addresses"""
        results = []
        for addr in addresses:
            category, confidence = self.classify(addr)
            results.append((addr, category, confidence))
        return results
    
    def get_top_whales(self, n: int = 50, category: str = None) -> List[str]:
        """Get top n whale addresses, optionally filtered by category"""
        whales = []
        
        for addr, info in self.watched_whales.items():
            if category is None or info['category'] == category:
                whales.append((addr, info['balance_eth']))
        
        # Sort by balance
        whales.sort(key=lambda x: x[1], reverse=True)
        
        return [w[0] for w in whales[:n]]
    
    def get_class_distribution(self) -> dict:
        """Get distribution of whale categories"""
        distribution = {}
        for info in self.watched_whales.values():
            cat = info['category']
            distribution[cat] = distribution.get(cat, 0) + 1
        return distribution


# Quick test
if __name__ == "__main__":
    print("Testing WhaleClassifier...")
    
    # Try loading from local first
    local_dir = os.path.join(os.path.dirname(__file__), '..', 'models')
    
    if os.path.exists(local_dir):
        classifier = WhaleClassifier(local_model_dir=local_dir)
    else:
        classifier = WhaleClassifier()  # Load from GCS
    
    # Test classification
    test_address = "0x28c6c06298d514db089934071355e5743bf21d60"  # Binance
    category, confidence = classifier.classify(test_address)
    print(f"\nTest classification:")
    print(f"  Address: {test_address}")
    print(f"  Category: {category}")
    print(f"  Confidence: {confidence:.2%}")
    
    # Distribution
    print(f"\nClass distribution:")
    for cat, count in classifier.get_class_distribution().items():
        print(f"  {cat}: {count}")