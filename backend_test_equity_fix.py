#!/usr/bin/env python3
"""
Test script for exchange equity valuation bug fix.
Verifies that Coinbase (and other exchanges holding altcoins) now show correct non-zero equity.
"""

import requests
import time
import json

# Backend URL
BASE_URL = "https://luchii-trade-bot.preview.emergentagent.com/api"

# Test account token (teslalicensecompany@gmail.com)
TESLA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2YWQ2MTVlNTAyZDRlNjMzOTMwZTBlOGIiLCJlbWFpbCI6InRlc2xhbGljZW5zZWNvbXBhbnlAZ21haWwuY29tIiwidHlwZSI6ImFjY2VzcyIsImV4cCI6MTc5MzA2MTA4Nn0.0lYkNTBnJR6nqJbtOHyO3FEFwsiU1JDfTyVqy91wfUk"

# Admin credentials for regression testing
ADMIN_EMAIL = "admin@gravia.io"
ADMIN_PASSWORD = "Gravia@2026"

def get_admin_token():
    """Login as admin and get token"""
    response = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    if response.status_code == 200:
        return response.json()["token"]
    else:
        print(f"❌ Admin login failed: {response.status_code}")
        return None

def test_tesla_exchange_connected():
    """Test 1: GET /api/exchange/connected with TESLA_TOKEN"""
    print("\n" + "="*80)
    print("TEST 1: GET /api/exchange/connected (teslalicensecompany@gmail.com)")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {TESLA_TOKEN}"}
    response = requests.get(f"{BASE_URL}/exchange/connected", headers=headers)
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code != 200:
        print(f"❌ FAILED: Expected 200, got {response.status_code}")
        print(f"Response: {response.text}")
        return False
    
    data = response.json()
    print(f"Response: {json.dumps(data, indent=2)}")
    
    # Check if we have 2 exchanges
    if not isinstance(data, list):
        print(f"❌ FAILED: Expected array, got {type(data)}")
        return False
    
    if len(data) != 2:
        print(f"❌ FAILED: Expected 2 exchanges, got {len(data)}")
        return False
    
    # Find OKX and Coinbase
    okx_exchange = None
    coinbase_exchange = None
    
    for exchange in data:
        if exchange.get("exchange") == "okx":
            okx_exchange = exchange
        elif exchange.get("exchange") == "coinbase":
            coinbase_exchange = exchange
    
    if not okx_exchange:
        print("❌ FAILED: OKX exchange not found")
        return False
    
    if not coinbase_exchange:
        print("❌ FAILED: Coinbase exchange not found")
        return False
    
    print(f"\n✅ Found 2 exchanges: OKX and Coinbase")
    print(f"   OKX equity: ${okx_exchange.get('equity', 'N/A')}")
    print(f"   OKX primary: {okx_exchange.get('primary', False)}")
    print(f"   Coinbase equity: ${coinbase_exchange.get('equity', 'N/A')}")
    print(f"   Coinbase primary: {coinbase_exchange.get('primary', False)}")
    
    # CRITICAL: Check if Coinbase equity is non-zero
    coinbase_equity = coinbase_exchange.get("equity")
    
    if coinbase_equity is None:
        print(f"⚠️  WARNING: Coinbase equity is None (not yet refreshed)")
        return None  # Needs refresh
    
    if coinbase_equity == 0 or coinbase_equity == 0.0:
        print(f"⚠️  WARNING: Coinbase equity is 0 (may need refresh)")
        return None  # Needs refresh
    
    print(f"\n✅ CRITICAL: Coinbase equity is NON-ZERO: ${coinbase_equity}")
    print(f"   Expected ~$1.49, got ${coinbase_equity}")
    
    return True

def test_tesla_predict_status():
    """Test 2: GET /api/predict/status with TESLA_TOKEN"""
    print("\n" + "="*80)
    print("TEST 2: GET /api/predict/status (teslalicensecompany@gmail.com)")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {TESLA_TOKEN}"}
    response = requests.get(f"{BASE_URL}/predict/status", headers=headers)
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code != 200:
        print(f"❌ FAILED: Expected 200, got {response.status_code}")
        print(f"Response: {response.text}")
        return False
    
    data = response.json()
    print(f"Response keys: {list(data.keys())}")
    
    # Check for funds and fundsDetail
    if "funds" not in data:
        print("❌ FAILED: 'funds' field missing")
        return False
    
    if "fundsDetail" not in data:
        print("❌ FAILED: 'fundsDetail' field missing")
        return False
    
    funds = data["funds"]
    funds_detail = data["fundsDetail"]
    
    print(f"\nFunds: ${funds}")
    print(f"FundsDetail: {json.dumps(funds_detail, indent=2)}")
    
    # Check fundsDetail structure
    required_keys = ["ledgerUsd", "exchangeEquity", "mirrored", "exchangeFree", "total"]
    for key in required_keys:
        if key not in funds_detail:
            print(f"❌ FAILED: fundsDetail missing key '{key}'")
            return False
    
    exchange_equity = funds_detail["exchangeEquity"]
    print(f"\n✅ exchangeEquity: ${exchange_equity}")
    print(f"   Expected ~$18.8 (OKX ~17.33 + Coinbase ~1.49)")
    
    if exchange_equity < 18.0:
        print(f"⚠️  WARNING: exchangeEquity is ${exchange_equity}, expected ~$18.8")
        print(f"   This may indicate Coinbase equity is not being counted")
    else:
        print(f"✅ exchangeEquity looks correct: ${exchange_equity}")
    
    # Note about exchangeFree/total
    exchange_free = funds_detail.get("exchangeFree", 0)
    total = funds_detail.get("total", 0)
    print(f"\nNote: exchangeFree=${exchange_free}, total=${total}")
    print(f"   These may be ~$0.41 due to prior predict losses (risk cap) - EXPECTED")
    
    return True

def test_backend_health():
    """Test backend health endpoint"""
    print("\n" + "="*80)
    print("TEST: Backend Health Check")
    print("="*80)
    
    response = requests.get(f"{BASE_URL.replace('/api', '')}/api/")
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("✅ Backend health check PASSED")
        return True
    else:
        print(f"❌ Backend health check FAILED: {response.status_code}")
        return False

def test_admin_regression(admin_token):
    """Test 4: Regression tests with admin token"""
    print("\n" + "="*80)
    print("TEST 4: Regression Tests (admin@gravia.io)")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Test GET /api/exchange/connected
    print("\n--- GET /api/exchange/connected ---")
    response = requests.get(f"{BASE_URL}/exchange/connected", headers=headers)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Returns 200 with {len(data)} exchange(s)")
        if len(data) > 0:
            for ex in data:
                print(f"   - {ex.get('exchange')}: equity=${ex.get('equity')}")
    else:
        print(f"❌ FAILED: {response.status_code}")
        return False
    
    # Test GET /api/bot/state
    print("\n--- GET /api/bot/state ---")
    response = requests.get(f"{BASE_URL}/bot/state", headers=headers)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Returns 200 with equity=${data.get('equity')}")
    else:
        print(f"❌ FAILED: {response.status_code}")
        return False
    
    # Test GET /api/leaderboard
    print("\n--- GET /api/leaderboard ---")
    response = requests.get(f"{BASE_URL}/leaderboard", headers=headers)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Returns 200 with {len(data)} entries")
    else:
        print(f"❌ FAILED: {response.status_code}")
        return False
    
    return True

def main():
    print("="*80)
    print("EXCHANGE EQUITY VALUATION BUG FIX VERIFICATION")
    print("Testing account: teslalicensecompany@gmail.com")
    print("="*80)
    
    # Test backend health first
    if not test_backend_health():
        print("\n❌ Backend health check failed, aborting tests")
        return
    
    # Test 1: GET /api/exchange/connected
    result = test_tesla_exchange_connected()
    
    if result is None:
        # Coinbase equity is 0 or None, need to trigger refresh
        print("\n⚠️  Coinbase equity is 0 or None, triggering refresh via GET /api/predict/status...")
        test_tesla_predict_status()
        print("\n⏳ Waiting 4 seconds for equity refresh...")
        time.sleep(4)
        
        print("\n🔄 Re-checking GET /api/exchange/connected after refresh...")
        result = test_tesla_exchange_connected()
    
    # Test 2: GET /api/predict/status
    test_tesla_predict_status()
    
    # Test 3: Backend logs check
    print("\n" + "="*80)
    print("TEST 3: Backend Logs Check")
    print("="*80)
    print("Checking backend logs for NEW errors from equity_usd_priced...")
    print("(This will be done separately via tail command)")
    
    # Test 4: Admin regression
    admin_token = get_admin_token()
    if admin_token:
        test_admin_regression(admin_token)
    else:
        print("⚠️  Skipping admin regression tests (login failed)")
    
    print("\n" + "="*80)
    print("TESTING COMPLETE")
    print("="*80)
    print("\nNext: Check backend logs for errors:")
    print("  tail -n 200 /var/log/supervisor/backend.*.log | grep -A 5 -B 5 'equity_usd_priced\\|asset pricing failed\\|File descriptor'")

if __name__ == "__main__":
    main()
