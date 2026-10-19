#!/usr/bin/env python3
"""Backend API tests for Luchii Predict Streak Alerts regression testing."""
import asyncio
import httpx
import os
import sys

# Backend URL from frontend/.env
BASE_URL = "https://219a8099-f237-4e82-b89c-0a6b6e9b61a1.preview.emergentagent.com/api"

# Admin credentials from test_credentials.md
ADMIN_EMAIL = "admin@gravia.io"
ADMIN_PASSWORD = "Gravia@2026"

class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []
    
    def add_pass(self, test_name: str, details: str = ""):
        self.passed.append(f"✅ {test_name}" + (f": {details}" if details else ""))
    
    def add_fail(self, test_name: str, details: str):
        self.failed.append(f"❌ {test_name}: {details}")
    
    def add_warning(self, test_name: str, details: str):
        self.warnings.append(f"⚠️  {test_name}: {details}")
    
    def print_summary(self):
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        
        if self.passed:
            print("\n✅ PASSED:")
            for p in self.passed:
                print(f"  {p}")
        
        if self.warnings:
            print("\n⚠️  WARNINGS:")
            for w in self.warnings:
                print(f"  {w}")
        
        if self.failed:
            print("\n❌ FAILED:")
            for f in self.failed:
                print(f"  {f}")
        
        print("\n" + "="*80)
        print(f"Total: {len(self.passed)} passed, {len(self.warnings)} warnings, {len(self.failed)} failed")
        print("="*80 + "\n")
        
        return len(self.failed) == 0

async def test_predict_endpoints():
    """Test Luchii Predict endpoints after adding streak alerts."""
    result = TestResult()
    token = None
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Test 1: Backend health check
        print("\n[TEST 1] Backend health check...")
        try:
            resp = await client.get(f"{BASE_URL}/")
            if resp.status_code == 200 and resp.json().get("ok"):
                result.add_pass("Backend health", f"Status: {resp.status_code}, Response: {resp.json()}")
            else:
                result.add_fail("Backend health", f"Unexpected response: {resp.status_code} {resp.text}")
        except Exception as e:
            result.add_fail("Backend health", f"Exception: {str(e)}")
            return result
        
        # Test 2: Admin login
        print("\n[TEST 2] Admin login...")
        try:
            resp = await client.post(f"{BASE_URL}/auth/login", json={
                "email": ADMIN_EMAIL,
                "password": ADMIN_PASSWORD
            })
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("token")
                user = data.get("user", {})
                result.add_pass("Admin login", f"User: {user.get('email')}, Role: {user.get('role')}")
            else:
                result.add_fail("Admin login", f"Status: {resp.status_code}, Response: {resp.text}")
                return result
        except Exception as e:
            result.add_fail("Admin login", f"Exception: {str(e)}")
            return result
        
        if not token:
            result.add_fail("Admin login", "No token received")
            return result
        
        headers = {"Authorization": f"Bearer {token}"}
        
        # Test 3: GET /api/predict/status
        print("\n[TEST 3] GET /api/predict/status...")
        try:
            resp = await client.get(f"{BASE_URL}/predict/status", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                enabled = data.get("enabled")
                assets = data.get("assets", [])
                durations = data.get("durations", [])
                quotes = data.get("quotes", {})
                
                if enabled:
                    result.add_pass("Predict status - enabled", f"enabled={enabled}")
                else:
                    result.add_fail("Predict status - enabled", f"PREDICT_ENABLED should be true, got: {enabled}")
                
                if assets:
                    result.add_pass("Predict status - assets", f"Assets: {assets}")
                else:
                    result.add_fail("Predict status - assets", "No assets returned")
                
                if durations:
                    result.add_pass("Predict status - durations", f"Durations: {durations}")
                else:
                    result.add_fail("Predict status - durations", "No durations returned")
                
                if quotes:
                    result.add_pass("Predict status - quotes", f"Quotes for {len(quotes)} assets")
                    # Check quote structure
                    for asset, quote in quotes.items():
                        if "price" in quote and "confidence" in quote:
                            print(f"  {asset}: price={quote.get('price')}, confidence={quote.get('confidence')}, aiSignal={quote.get('aiSignal')}")
                        else:
                            result.add_warning("Predict status - quote structure", f"{asset} missing price or confidence")
                else:
                    result.add_fail("Predict status - quotes", "No quotes returned")
            else:
                result.add_fail("Predict status", f"Status: {resp.status_code}, Response: {resp.text}")
        except Exception as e:
            result.add_fail("Predict status", f"Exception: {str(e)}")
        
        # Test 4: POST /api/predict/contracts (place a contract)
        print("\n[TEST 4] POST /api/predict/contracts (place BTC UP contract)...")
        try:
            resp = await client.post(f"{BASE_URL}/predict/contracts", headers=headers, json={
                "asset": "BTC",
                "direction": "UP",
                "stake": 1.0,
                "duration": 3
            })
            
            # Accept 201 (success), 400/402/409/422 (insufficient funds/liability - expected)
            if resp.status_code == 201:
                data = resp.json()
                result.add_pass("Place contract", f"Contract placed: {data.get('id')}, stake=${data.get('stake')}, multiplier={data.get('multiplier')}x")
                print(f"  Contract details: asset={data.get('asset')}, direction={data.get('direction')}, status={data.get('status')}")
            elif resp.status_code in [400, 402, 409, 422]:
                data = resp.json()
                # Handle both direct and nested error structures
                detail = data.get("detail", {})
                if isinstance(detail, dict):
                    code = detail.get("code", "UNKNOWN")
                    msg = detail.get("message", resp.text)
                else:
                    code = data.get("code", "UNKNOWN")
                    msg = data.get("message", str(detail) if detail else resp.text)
                
                # Check if it's an insufficient balance error
                full_response = str(data)
                if "Insufficient balance" in full_response or "insufficient" in full_response.lower() or code in ["LEDGER_ERROR", "INSUFFICIENT_FUNDS", "LIABILITY_CAP", "STAKE_RANGE"]:
                    result.add_warning("Place contract", f"Expected business error (status {resp.status_code}): {code} - {msg}")
                    print(f"  This is ACCEPTABLE - admin account likely has no USD balance")
                else:
                    result.add_fail("Place contract", f"Unexpected error (status {resp.status_code}): {code} - {msg}")
            else:
                result.add_fail("Place contract", f"Unexpected status: {resp.status_code}, Response: {resp.text}")
        except Exception as e:
            result.add_fail("Place contract", f"Exception: {str(e)}")
        
        # Test 5: GET /api/predict/contracts (list contracts)
        print("\n[TEST 5] GET /api/predict/contracts (list contracts)...")
        try:
            resp = await client.get(f"{BASE_URL}/predict/contracts", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                stats = data.get("stats", {})
                result.add_pass("List contracts", f"Found {len(items)} contracts, Stats: {stats}")
                if items:
                    print(f"  Recent contracts:")
                    for c in items[:3]:  # Show first 3
                        print(f"    - {c.get('asset')} {c.get('direction')} ${c.get('stake')} @ {c.get('multiplier')}x - {c.get('status')}")
            else:
                result.add_fail("List contracts", f"Status: {resp.status_code}, Response: {resp.text}")
        except Exception as e:
            result.add_fail("List contracts", f"Exception: {str(e)}")
        
        # Test 6: Backend health check after tests
        print("\n[TEST 6] Backend health check after tests...")
        try:
            resp = await client.get(f"{BASE_URL}/")
            if resp.status_code == 200 and resp.json().get("ok"):
                result.add_pass("Backend health after tests", "Backend still healthy")
            else:
                result.add_fail("Backend health after tests", f"Backend unhealthy: {resp.status_code} {resp.text}")
        except Exception as e:
            result.add_fail("Backend health after tests", f"Exception: {str(e)}")
    
    return result

async def main():
    print("="*80)
    print("LUCHII PREDICT STREAK ALERTS - REGRESSION TEST")
    print("="*80)
    print(f"Backend URL: {BASE_URL}")
    print(f"Admin: {ADMIN_EMAIL}")
    print("="*80)
    
    result = await test_predict_endpoints()
    success = result.print_summary()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
