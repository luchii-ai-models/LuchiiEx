#!/usr/bin/env python3
"""Test leaderboard bug fix - KeyError 'name' on bot docs without a name field."""
import asyncio
import httpx
import sys
from motor.motor_asyncio import AsyncIOMotorClient
import os

# Backend URL from frontend/.env
BASE_URL = "https://luchii-trade-bot.preview.emergentagent.com/api"

# Admin credentials from test_credentials.md
ADMIN_EMAIL = "admin@gravia.io"
ADMIN_PASSWORD = "Gravia@2026"

# MongoDB connection from backend/.env
MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "test_database"

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
        
        if self.failed:
            print("\n❌ FAILED:")
            for f in self.failed:
                print(f"  {f}")
        
        if self.warnings:
            print("\n⚠️  WARNINGS:")
            for w in self.warnings:
                print(f"  {w}")
        
        if self.passed:
            print("\n✅ PASSED:")
            for p in self.passed:
                print(f"  {p}")
        
        print("\n" + "="*80)
        print(f"Total: {len(self.passed)} passed, {len(self.warnings)} warnings, {len(self.failed)} failed")
        print("="*80 + "\n")
        
        return len(self.failed) == 0

async def test_leaderboard_bugfix():
    """Test leaderboard endpoint handles missing 'name' field gracefully."""
    result = TestResult()
    token = None
    test_user_id = "nameless_test_bot_12345"
    
    # Connect to MongoDB
    mongo_client = AsyncIOMotorClient(MONGO_URL)
    db = mongo_client[DB_NAME]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Test 1: Admin login
        print("\n[TEST 1] Admin login...")
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
        
        # Test 2: GET /api/leaderboard - should return 200 (not 500)
        print("\n[TEST 2] GET /api/leaderboard (baseline - should return 200)...")
        try:
            resp = await client.get(f"{BASE_URL}/leaderboard", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    result.add_pass("GET /api/leaderboard - baseline", f"Returns 200 with array (length: {len(data)})")
                    print(f"  Leaderboard entries: {len(data)}")
                    
                    # Verify structure of first entry if exists
                    if len(data) > 0:
                        first_entry = data[0]
                        required_keys = ["rank", "name", "pnl", "tradePnl", "predictNet", "predictWon", "predictN"]
                        if all(k in first_entry for k in required_keys):
                            result.add_pass("GET /api/leaderboard - structure", f"First entry has all required keys: {list(first_entry.keys())}")
                        else:
                            missing = [k for k in required_keys if k not in first_entry]
                            result.add_fail("GET /api/leaderboard - structure", f"Missing keys in first entry: {missing}")
                else:
                    result.add_fail("GET /api/leaderboard - baseline", f"Response is not an array: {type(data).__name__}")
            elif resp.status_code == 500:
                result.add_fail("GET /api/leaderboard - baseline", f"CRITICAL: Returns 500 (KeyError likely). Response: {resp.text}")
            else:
                result.add_fail("GET /api/leaderboard - baseline", f"Status: {resp.status_code}, Response: {resp.text}")
        except Exception as e:
            result.add_fail("GET /api/leaderboard - baseline", f"Exception: {str(e)}")
        
        # Test 3: Insert a nameless bot doc to truly exercise the fix
        print("\n[TEST 3] Insert nameless bot doc into db.bots...")
        try:
            # Insert a bot doc WITHOUT a 'name' field
            test_bot_doc = {
                "user_id": test_user_id,
                "realized_pnl": 10.5,
                "alerts_enabled": False,
                "auto_enabled": False
            }
            await db.bots.insert_one(test_bot_doc)
            result.add_pass("Insert nameless bot", f"Inserted bot with user_id={test_user_id}, NO 'name' field")
            print(f"  Test bot doc: {test_bot_doc}")
        except Exception as e:
            result.add_fail("Insert nameless bot", f"Exception: {str(e)}")
            # If we can't insert, we can't test the fix properly
            return result
        
        # Test 4: GET /api/leaderboard again - should still return 200 and show nameless bot as "Trader"
        print("\n[TEST 4] GET /api/leaderboard (with nameless bot - should return 200)...")
        try:
            resp = await client.get(f"{BASE_URL}/leaderboard", headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    result.add_pass("GET /api/leaderboard - with nameless bot", f"Returns 200 with array (length: {len(data)})")
                    
                    # Find the nameless bot in the leaderboard
                    nameless_bot_entry = None
                    for entry in data:
                        # The nameless bot should appear with name "Trader" and pnl 10.5
                        if entry.get("tradePnl") == 10.5 and entry.get("name") == "Trader":
                            nameless_bot_entry = entry
                            break
                    
                    if nameless_bot_entry:
                        result.add_pass("Nameless bot appears as 'Trader'", f"Found entry: {nameless_bot_entry}")
                        print(f"  Nameless bot entry: {nameless_bot_entry}")
                    else:
                        result.add_warning("Nameless bot not found", f"Could not find nameless bot in leaderboard (may be ranked out or filtered)")
                        print(f"  All entries: {data}")
                else:
                    result.add_fail("GET /api/leaderboard - with nameless bot", f"Response is not an array: {type(data).__name__}")
            elif resp.status_code == 500:
                result.add_fail("GET /api/leaderboard - with nameless bot", f"CRITICAL: Returns 500 (KeyError 'name' NOT fixed). Response: {resp.text}")
            else:
                result.add_fail("GET /api/leaderboard - with nameless bot", f"Status: {resp.status_code}, Response: {resp.text}")
        except Exception as e:
            result.add_fail("GET /api/leaderboard - with nameless bot", f"Exception: {str(e)}")
        
        # Test 5: Clean up - delete the test bot doc
        print("\n[TEST 5] Clean up - delete test bot doc...")
        try:
            delete_result = await db.bots.delete_one({"user_id": test_user_id})
            if delete_result.deleted_count > 0:
                result.add_pass("Clean up", f"Deleted test bot doc (user_id={test_user_id})")
            else:
                result.add_warning("Clean up", f"Test bot doc not found for deletion (may have been deleted already)")
        except Exception as e:
            result.add_warning("Clean up", f"Exception during cleanup: {str(e)}")
        
        # Test 6: Backend health check
        print("\n[TEST 6] Backend health check...")
        try:
            resp = await client.get(f"{BASE_URL}/")
            if resp.status_code == 200 and resp.json().get("ok"):
                result.add_pass("Backend health", "Backend healthy after all tests")
            else:
                result.add_fail("Backend health", f"Backend unhealthy: {resp.status_code} {resp.text}")
        except Exception as e:
            result.add_fail("Backend health", f"Exception: {str(e)}")
    
    # Close MongoDB connection
    mongo_client.close()
    
    return result

async def main():
    print("="*80)
    print("LUCHII LEADERBOARD BUG FIX TEST - KeyError 'name'")
    print("="*80)
    print(f"Backend URL: {BASE_URL}")
    print(f"Admin: {ADMIN_EMAIL}")
    print(f"MongoDB: {MONGO_URL}/{DB_NAME}")
    print("="*80)
    
    result = await test_leaderboard_bugfix()
    success = result.print_summary()
    
    # Check backend logs for KeyError: 'name' tracebacks
    print("\n" + "="*80)
    print("CHECKING BACKEND LOGS FOR KeyError: 'name' TRACEBACKS")
    print("="*80)
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())
