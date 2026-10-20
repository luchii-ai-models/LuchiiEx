#!/usr/bin/env python3
"""
Regression test for altcoin pricing hardening in exchange_service.equity_usd_priced
Tests the route-through-BTC/ETH for coins with no direct USD/USDT/USDC market
"""
import requests
import time
import json
from datetime import datetime

# Backend URL
BASE_URL = "https://luchii-trade-bot.preview.emergentagent.com/api"

# Test tokens
TESLA_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiI2YWQ2MTVlNTAyZDRlNjMzOTMwZTBlOGIiLCJlbWFpbCI6InRlc2xhbGljZW5zZWNvbXBhbnlAZ21haWwuY29tIiwidHlwZSI6ImFjY2VzcyIsImV4cCI6MTc5MzA2MTA4Nn0.0lYkNTBnJR6nqJbtOHyO3FEFwsiU1JDfTyVqy91wfUk"

# Admin credentials for regression tests
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

def test_1_tesla_account_equity():
    """Test 1: Verify Tesla account shows correct equity for both exchanges"""
    print("\n" + "="*80)
    print("TEST 1: Tesla Account Equity Verification")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {TESLA_TOKEN}"}
    
    # First trigger a fresh equity refresh
    print("\n1a. Triggering fresh equity refresh via GET /api/predict/status...")
    response = requests.get(f"{BASE_URL}/predict/status", headers=headers)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ GET /api/predict/status returned 200")
        print(f"   funds: ${data.get('funds', 'N/A')}")
        if 'fundsDetail' in data:
            funds_detail = data['fundsDetail']
            print(f"   fundsDetail.exchangeEquity: ${funds_detail.get('exchangeEquity', 'N/A')}")
            print(f"   fundsDetail.ledgerUsd: ${funds_detail.get('ledgerUsd', 'N/A')}")
            print(f"   fundsDetail.total: ${funds_detail.get('total', 'N/A')}")
            print(f"   fundsDetail.exchangeFree: ${funds_detail.get('exchangeFree', 'N/A')}")
            
            exchange_equity = funds_detail.get('exchangeEquity', 0)
            if 18.0 <= exchange_equity <= 19.5:
                print(f"✅ exchangeEquity ${exchange_equity} is in expected range (~$18.8)")
            else:
                print(f"⚠️  exchangeEquity ${exchange_equity} is outside expected range $18.0-$19.5")
    else:
        print(f"❌ GET /api/predict/status failed: {response.status_code}")
        print(f"   Response: {response.text}")
    
    # Wait a moment for equity refresh to complete
    print("\n   Waiting 4 seconds for equity refresh to complete...")
    time.sleep(4)
    
    # Now check connected exchanges
    print("\n1b. Checking GET /api/exchange/connected...")
    response = requests.get(f"{BASE_URL}/exchange/connected", headers=headers)
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        exchanges = response.json()
        print(f"✅ GET /api/exchange/connected returned 200")
        print(f"   Number of exchanges: {len(exchanges)}")
        
        okx_equity = None
        coinbase_equity = None
        
        for ex in exchanges:
            exchange_name = ex.get('exchange', 'unknown')
            equity = ex.get('equity', 0)
            primary = ex.get('primary', False)
            print(f"\n   Exchange: {exchange_name}")
            print(f"   - equity: ${equity}")
            print(f"   - primary: {primary}")
            print(f"   - hostname: {ex.get('hostname', 'N/A')}")
            
            if exchange_name == 'okx':
                okx_equity = equity
            elif exchange_name == 'coinbase':
                coinbase_equity = equity
        
        # Verify OKX equity
        if okx_equity is not None:
            if 16.0 <= okx_equity <= 18.5:
                print(f"\n✅ OKX equity ${okx_equity} is in expected range (~$17.33)")
            else:
                print(f"\n⚠️  OKX equity ${okx_equity} is outside expected range $16.0-$18.5")
        else:
            print(f"\n❌ OKX exchange not found in connected exchanges")
        
        # CRITICAL: Verify Coinbase equity is NON-ZERO
        if coinbase_equity is not None:
            if coinbase_equity > 0:
                print(f"✅ CRITICAL: Coinbase equity ${coinbase_equity} is NON-ZERO (expected ~$1.49)")
                if 1.0 <= coinbase_equity <= 2.0:
                    print(f"✅ Coinbase equity is in expected range $1.0-$2.0")
                else:
                    print(f"⚠️  Coinbase equity ${coinbase_equity} is outside expected range $1.0-$2.0")
            else:
                print(f"❌ CRITICAL FAILURE: Coinbase equity is ${coinbase_equity} (should be ~$1.49)")
                print(f"   The altcoin pricing fix is NOT working!")
        else:
            print(f"❌ Coinbase exchange not found in connected exchanges")
        
        # Summary
        print(f"\n📊 SUMMARY:")
        print(f"   OKX equity: ${okx_equity if okx_equity is not None else 'NOT FOUND'}")
        print(f"   Coinbase equity: ${coinbase_equity if coinbase_equity is not None else 'NOT FOUND'}")
        if okx_equity and coinbase_equity:
            total = okx_equity + coinbase_equity
            print(f"   Total: ${total:.2f}")
    else:
        print(f"❌ GET /api/exchange/connected failed: {response.status_code}")
        print(f"   Response: {response.text}")

def test_2_rapid_polling():
    """Test 2: Hit GET /api/predict/status 5 times rapidly"""
    print("\n" + "="*80)
    print("TEST 2: Rapid Polling (5x GET /api/predict/status)")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {TESLA_TOKEN}"}
    
    results = []
    start_time = time.time()
    
    for i in range(5):
        req_start = time.time()
        response = requests.get(f"{BASE_URL}/predict/status", headers=headers)
        req_time = time.time() - req_start
        
        results.append({
            'attempt': i + 1,
            'status': response.status_code,
            'time': req_time,
            'success': response.status_code == 200
        })
        
        print(f"Request {i+1}: {response.status_code} ({req_time:.3f}s)")
    
    total_time = time.time() - start_time
    
    # Summary
    print(f"\n📊 SUMMARY:")
    print(f"   Total time: {total_time:.3f}s")
    print(f"   Average time per request: {total_time/5:.3f}s")
    
    all_success = all(r['success'] for r in results)
    if all_success:
        print(f"✅ All 5 requests returned 200 - NO HANGS")
    else:
        failed = [r for r in results if not r['success']]
        print(f"❌ {len(failed)} requests failed:")
        for r in failed:
            print(f"   Request {r['attempt']}: {r['status']}")

def test_3_backend_health():
    """Test 3: Backend health check"""
    print("\n" + "="*80)
    print("TEST 3: Backend Health Check")
    print("="*80)
    
    response = requests.get(f"{BASE_URL}/")
    print(f"GET /api/ status: {response.status_code}")
    
    if response.status_code == 200:
        print(f"✅ Backend health check passed")
        try:
            data = response.json()
            print(f"   Response: {json.dumps(data, indent=2)}")
        except:
            print(f"   Response: {response.text}")
    else:
        print(f"❌ Backend health check failed: {response.status_code}")

def test_4_admin_regression():
    """Test 4: Regression tests with admin account"""
    print("\n" + "="*80)
    print("TEST 4: Admin Account Regression Tests")
    print("="*80)
    
    admin_token = get_admin_token()
    if not admin_token:
        print("❌ Cannot proceed with admin tests - login failed")
        return
    
    headers = {"Authorization": f"Bearer {admin_token}"}
    
    # Test endpoints
    endpoints = [
        ("/bot/state", "GET /api/bot/state"),
        ("/leaderboard", "GET /api/leaderboard"),
        ("/exchange/connected", "GET /api/exchange/connected"),
        ("/research/latest", "GET /api/research/latest")
    ]
    
    results = []
    for endpoint, name in endpoints:
        response = requests.get(f"{BASE_URL}{endpoint}", headers=headers)
        success = response.status_code == 200
        results.append({
            'name': name,
            'status': response.status_code,
            'success': success
        })
        
        status_icon = "✅" if success else "❌"
        print(f"{status_icon} {name}: {response.status_code}")
        
        if not success:
            print(f"   Response: {response.text[:200]}")
    
    # Summary
    print(f"\n📊 SUMMARY:")
    passed = sum(1 for r in results if r['success'])
    print(f"   {passed}/{len(results)} regression tests passed")
    
    if passed == len(results):
        print(f"✅ All admin regression tests passed")
    else:
        failed = [r for r in results if not r['success']]
        print(f"❌ Failed tests:")
        for r in failed:
            print(f"   {r['name']}: {r['status']}")

def main():
    print("="*80)
    print("REGRESSION TEST: Altcoin Pricing Hardening")
    print("Testing exchange_service.equity_usd_priced with route-through-BTC/ETH")
    print("="*80)
    print(f"Start time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        test_1_tesla_account_equity()
        test_2_rapid_polling()
        test_3_backend_health()
        test_4_admin_regression()
        
        print("\n" + "="*80)
        print("ALL TESTS COMPLETED")
        print("="*80)
        print(f"End time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("\n⚠️  IMPORTANT: Check backend logs for any NEW errors during test window")
        print("   Command: tail -n 200 /var/log/supervisor/backend.*.log")
        
    except Exception as e:
        print(f"\n❌ Test suite failed with exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
