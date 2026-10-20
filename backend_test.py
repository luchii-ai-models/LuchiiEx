#!/usr/bin/env python3
"""
Backend concurrency test for Luchii - Verify the per-user asyncio.Lock fix
in engine.refresh_live_equity prevents "File descriptor X is used by transport" errors
when GET /api/predict/status is hit rapidly/concurrently.
"""
import asyncio
import aiohttp
import sys
import time
from typing import List, Dict, Any

# Backend URL from frontend/.env
BACKEND_URL = "https://luchii-trade-bot.preview.emergentagent.com/api"

# Admin credentials from test_credentials.md
ADMIN_EMAIL = "admin@gravia.io"
ADMIN_PASSWORD = "Gravia@2026"


class TestResult:
    def __init__(self):
        self.passed = []
        self.failed = []
        self.warnings = []
    
    def add_pass(self, msg: str):
        self.passed.append(msg)
        print(f"✅ {msg}")
    
    def add_fail(self, msg: str):
        self.failed.append(msg)
        print(f"❌ {msg}")
    
    def add_warning(self, msg: str):
        self.warnings.append(msg)
        print(f"⚠️  {msg}")
    
    def summary(self):
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        print(f"✅ Passed: {len(self.passed)}")
        print(f"❌ Failed: {len(self.failed)}")
        print(f"⚠️  Warnings: {len(self.warnings)}")
        
        if self.failed:
            print("\n❌ FAILED TESTS:")
            for f in self.failed:
                print(f"  - {f}")
        
        if self.warnings:
            print("\n⚠️  WARNINGS:")
            for w in self.warnings:
                print(f"  - {w}")
        
        return len(self.failed) == 0


async def login() -> str:
    """Login and return Bearer token"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{BACKEND_URL}/auth/login",
            json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Login failed: {resp.status} - {await resp.text()}")
            data = await resp.json()
            return data["token"]


async def test_concurrent_predict_status(token: str, result: TestResult):
    """
    Test 1: Hit GET /api/predict/status 10 times rapidly/concurrently.
    Every response must be 200 with numeric funds + fundsDetail.
    No request should hang or 500.
    """
    print("\n" + "="*80)
    print("TEST 1: Concurrent GET /api/predict/status (10 rapid requests)")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {token}"}
    
    async def single_request(session: aiohttp.ClientSession, idx: int) -> Dict[str, Any]:
        """Single request to /api/predict/status"""
        start = time.time()
        try:
            async with session.get(
                f"{BACKEND_URL}/predict/status",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                elapsed = time.time() - start
                status = resp.status
                data = await resp.json() if status == 200 else None
                return {
                    "idx": idx,
                    "status": status,
                    "data": data,
                    "elapsed": elapsed,
                    "error": None
                }
        except asyncio.TimeoutError:
            return {
                "idx": idx,
                "status": None,
                "data": None,
                "elapsed": time.time() - start,
                "error": "TIMEOUT"
            }
        except Exception as e:
            return {
                "idx": idx,
                "status": None,
                "data": None,
                "elapsed": time.time() - start,
                "error": str(e)
            }
    
    # Fire 10 concurrent requests as fast as possible
    async with aiohttp.ClientSession() as session:
        start_time = time.time()
        tasks = [single_request(session, i) for i in range(10)]
        responses = await asyncio.gather(*tasks)
        total_time = time.time() - start_time
    
    print(f"\nCompleted 10 requests in {total_time:.3f}s")
    
    # Analyze results
    success_count = 0
    error_count = 0
    timeout_count = 0
    non_200_count = 0
    missing_funds = 0
    missing_funds_detail = 0
    
    for r in responses:
        idx = r["idx"]
        status = r["status"]
        data = r["data"]
        elapsed = r["elapsed"]
        error = r["error"]
        
        if error == "TIMEOUT":
            timeout_count += 1
            result.add_fail(f"Request #{idx}: TIMEOUT after {elapsed:.3f}s")
        elif error:
            error_count += 1
            result.add_fail(f"Request #{idx}: ERROR - {error}")
        elif status != 200:
            non_200_count += 1
            result.add_fail(f"Request #{idx}: Status {status} (expected 200) - elapsed {elapsed:.3f}s")
        else:
            # Check response structure
            if data is None:
                result.add_fail(f"Request #{idx}: Status 200 but no JSON data")
                error_count += 1
            elif "funds" not in data:
                missing_funds += 1
                result.add_fail(f"Request #{idx}: Missing 'funds' field in response")
            elif "fundsDetail" not in data:
                missing_funds_detail += 1
                result.add_fail(f"Request #{idx}: Missing 'fundsDetail' field in response")
            elif not isinstance(data.get("funds"), (int, float)):
                result.add_fail(f"Request #{idx}: 'funds' is not numeric: {type(data.get('funds'))}")
            elif not isinstance(data.get("fundsDetail"), dict):
                result.add_fail(f"Request #{idx}: 'fundsDetail' is not a dict: {type(data.get('fundsDetail'))}")
            else:
                # Verify fundsDetail structure
                fd = data["fundsDetail"]
                required_keys = ["ledgerUsd", "exchangeEquity", "mirrored", "exchangeFree", "total"]
                missing_keys = [k for k in required_keys if k not in fd]
                
                if missing_keys:
                    result.add_fail(f"Request #{idx}: fundsDetail missing keys: {missing_keys}")
                else:
                    # All checks passed
                    success_count += 1
                    print(f"  Request #{idx}: ✅ 200 OK - funds={data['funds']}, elapsed={elapsed:.3f}s")
    
    # Summary
    print(f"\n📊 Results: {success_count}/10 successful, {error_count} errors, {timeout_count} timeouts, {non_200_count} non-200")
    
    if success_count == 10:
        result.add_pass("All 10 concurrent requests returned 200 with valid funds + fundsDetail")
    else:
        result.add_fail(f"Only {success_count}/10 requests succeeded")
    
    if timeout_count > 0:
        result.add_fail(f"{timeout_count} requests timed out (possible hang)")
    
    if non_200_count > 0:
        result.add_fail(f"{non_200_count} requests returned non-200 status")


async def check_backend_logs(result: TestResult):
    """
    Test 2: Check backend logs for NEW "File descriptor" / "used by transport" errors
    or unhandled tracebacks from engine.refresh_live_equity or _refresh_live_equity
    during the test window.
    """
    print("\n" + "="*80)
    print("TEST 2: Check backend logs for fd/transport errors")
    print("="*80)
    
    import subprocess
    
    # Check last 200 lines of backend logs
    try:
        result_err = subprocess.run(
            ["tail", "-n", "200", "/var/log/supervisor/backend.err.log"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        result_out = subprocess.run(
            ["tail", "-n", "200", "/var/log/supervisor/backend.out.log"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        logs = result_err.stdout + "\n" + result_out.stdout
        
        # Search for problematic patterns
        fd_errors = []
        transport_errors = []
        refresh_tracebacks = []
        
        lines = logs.split("\n")
        for i, line in enumerate(lines):
            lower = line.lower()
            
            # Check for "File descriptor X is used by transport"
            if "file descriptor" in lower and "transport" in lower:
                fd_errors.append(line.strip())
            
            # Check for "used by transport"
            if "used by transport" in lower:
                transport_errors.append(line.strip())
            
            # Check for tracebacks related to refresh_live_equity
            if "refresh_live_equity" in lower or "_refresh_live_equity" in lower:
                # Look for traceback context
                if "traceback" in lower or "error" in lower or "exception" in lower:
                    # Capture surrounding lines for context
                    start = max(0, i - 2)
                    end = min(len(lines), i + 3)
                    context = "\n".join(lines[start:end])
                    refresh_tracebacks.append(context)
        
        # Report findings
        print(f"\n📋 Log analysis (last 200 lines):")
        print(f"  - 'File descriptor' + 'transport' errors: {len(fd_errors)}")
        print(f"  - 'used by transport' errors: {len(transport_errors)}")
        print(f"  - refresh_live_equity tracebacks: {len(refresh_tracebacks)}")
        
        if fd_errors:
            result.add_fail(f"Found {len(fd_errors)} 'File descriptor...transport' errors in logs")
            for err in fd_errors[:3]:  # Show first 3
                print(f"    {err}")
        else:
            result.add_pass("No 'File descriptor...transport' errors found in logs")
        
        if transport_errors and not fd_errors:  # Don't double-count
            result.add_warning(f"Found {len(transport_errors)} 'used by transport' errors (may be unrelated)")
        
        if refresh_tracebacks:
            result.add_fail(f"Found {len(refresh_tracebacks)} tracebacks related to refresh_live_equity")
            for tb in refresh_tracebacks[:2]:  # Show first 2
                print(f"\n{tb}\n")
        else:
            result.add_pass("No tracebacks from refresh_live_equity/_refresh_live_equity")
        
        # Check for 500 errors
        error_500_count = sum(1 for line in lines if "500" in line and ("internal server error" in line.lower() or "traceback" in line.lower()))
        if error_500_count > 0:
            result.add_fail(f"Found {error_500_count} potential 500 errors in logs")
        else:
            result.add_pass("No 500 errors detected in logs")
        
    except subprocess.TimeoutExpired:
        result.add_warning("Log check timed out")
    except FileNotFoundError:
        result.add_warning("Backend log files not found at /var/log/supervisor/backend.*.log")
    except Exception as e:
        result.add_warning(f"Could not check logs: {e}")


async def test_regression_endpoints(token: str, result: TestResult):
    """
    Test 3: Regression tests
    - GET /api/bot/state = 200
    - GET /api/leaderboard = 200
    - GET /api/exchange/connected = 200 (array)
    """
    print("\n" + "="*80)
    print("TEST 3: Regression endpoints")
    print("="*80)
    
    headers = {"Authorization": f"Bearer {token}"}
    
    async with aiohttp.ClientSession() as session:
        # Test /api/bot/state
        try:
            async with session.get(f"{BACKEND_URL}/bot/state", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result.add_pass(f"GET /api/bot/state returned 200 (equity={data.get('equity')})")
                else:
                    result.add_fail(f"GET /api/bot/state returned {resp.status} (expected 200)")
        except Exception as e:
            result.add_fail(f"GET /api/bot/state failed: {e}")
        
        # Test /api/leaderboard
        try:
            async with session.get(f"{BACKEND_URL}/leaderboard", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result.add_pass(f"GET /api/leaderboard returned 200 ({len(data)} entries)")
                else:
                    result.add_fail(f"GET /api/leaderboard returned {resp.status} (expected 200)")
        except Exception as e:
            result.add_fail(f"GET /api/leaderboard failed: {e}")
        
        # Test /api/exchange/connected
        try:
            async with session.get(f"{BACKEND_URL}/exchange/connected", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list):
                        result.add_pass(f"GET /api/exchange/connected returned 200 (array with {len(data)} exchanges)")
                    else:
                        result.add_fail(f"GET /api/exchange/connected returned 200 but not an array: {type(data)}")
                else:
                    result.add_fail(f"GET /api/exchange/connected returned {resp.status} (expected 200)")
        except Exception as e:
            result.add_fail(f"GET /api/exchange/connected failed: {e}")


async def test_health_check(result: TestResult):
    """Test GET /api/ health check"""
    print("\n" + "="*80)
    print("TEST 4: Health check")
    print("="*80)
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"{BACKEND_URL}/", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("ok"):
                        result.add_pass("GET /api/ health check returned 200 with ok=true")
                    else:
                        result.add_warning(f"GET /api/ returned 200 but ok={data.get('ok')}")
                else:
                    result.add_fail(f"GET /api/ health check returned {resp.status} (expected 200)")
        except Exception as e:
            result.add_fail(f"GET /api/ health check failed: {e}")


async def main():
    print("="*80)
    print("LUCHII BACKEND CONCURRENCY TEST")
    print("Testing: engine.refresh_live_equity per-user asyncio.Lock fix")
    print("="*80)
    
    result = TestResult()
    
    try:
        # Login
        print("\n🔐 Logging in as admin...")
        token = await login()
        print(f"✅ Login successful")
        
        # Run tests
        await test_concurrent_predict_status(token, result)
        await check_backend_logs(result)
        await test_regression_endpoints(token, result)
        await test_health_check(result)
        
    except Exception as e:
        result.add_fail(f"Test suite failed: {e}")
        import traceback
        traceback.print_exc()
    
    # Print summary
    success = result.summary()
    
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
