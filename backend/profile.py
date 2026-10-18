"""User profile / settings / KYC — separate from the ledger user record."""
from typing import Literal, Optional

from pydantic import BaseModel, Field

from db import db, now_ms

PROFILE_DEFAULTS = {"username": None, "firstName": None, "lastName": None, "avatarUrl": None, "phoneNumber": None, "countryCode": None,
                    "timezone": "America/Los_Angeles", "locale": "en-US", "preferredCurrency": "USD"}
SETTINGS_DEFAULTS = {"darkMode": True, "emailNotifications": True, "smsNotifications": False, "pushNotifications": True, "hideBalances": False,
                     "autoCompound": True, "copyTradingEnabled": True, "preferredLeverage": 1, "riskLevel": "MEDIUM", "defaultTradingCurrency": "USDT"}


class ProfileUpdate(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    firstName: Optional[str] = Field(None, max_length=100)
    lastName: Optional[str] = Field(None, max_length=100)
    avatarUrl: Optional[str] = Field(None, max_length=2000)
    phoneNumber: Optional[str] = Field(None, max_length=50)
    countryCode: Optional[str] = Field(None, max_length=5)
    timezone: Optional[str] = Field(None, max_length=100)
    locale: Optional[str] = Field(None, max_length=20)
    preferredCurrency: Optional[Literal["USD", "USDT", "BTC", "ETH"]] = None


class SettingsUpdate(BaseModel):
    darkMode: Optional[bool] = None
    emailNotifications: Optional[bool] = None
    smsNotifications: Optional[bool] = None
    pushNotifications: Optional[bool] = None
    hideBalances: Optional[bool] = None
    autoCompound: Optional[bool] = None
    copyTradingEnabled: Optional[bool] = None
    preferredLeverage: Optional[int] = Field(None, ge=1, le=10)
    riskLevel: Optional[Literal["LOW", "MEDIUM", "HIGH"]] = None
    defaultTradingCurrency: Optional[Literal["USD", "USDT", "BTC"]] = None


class KycSubmit(BaseModel):
    legalName: str = Field(min_length=2, max_length=255)
    dateOfBirth: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    countryCode: Optional[str] = Field(None, max_length=5)


async def get_profile(user: dict) -> dict:
    p = await db.user_profiles.find_one({"user_id": user["id"]}, {"_id": 0}) or {}
    return {**PROFILE_DEFAULTS, **p.get("data", {}), "id": user["id"], "email": user["email"], "role": user["role"],
            "createdAt": p.get("created_at"), "updatedAt": p.get("updated_at")}


async def update_profile(user: dict, body: ProfileUpdate) -> dict:
    upd = {f"data.{k}": v for k, v in body.model_dump(exclude_none=True).items()}
    if "data.username" in upd and await db.user_profiles.find_one({"data.username": upd["data.username"], "user_id": {"$ne": user["id"]}}):
        raise ValueError("Username already taken")
    await db.user_profiles.update_one({"user_id": user["id"]}, {"$set": {**upd, "updated_at": now_ms()}, "$setOnInsert": {"created_at": now_ms()}}, upsert=True)
    return await get_profile(user)


async def get_settings(uid: str) -> dict:
    s = await db.user_settings.find_one({"user_id": uid}, {"_id": 0}) or {}
    return {**SETTINGS_DEFAULTS, **s.get("data", {})}


async def update_settings(uid: str, body: SettingsUpdate) -> dict:
    upd = {f"data.{k}": v for k, v in body.model_dump(exclude_none=True).items()}
    if upd:
        await db.user_settings.update_one({"user_id": uid}, {"$set": {**upd, "updated_at": now_ms()}}, upsert=True)
    return await get_settings(uid)


async def get_kyc(uid: str) -> dict:
    k = await db.user_kyc_profiles.find_one({"user_id": uid}, {"_id": 0}) or {}
    return {"status": k.get("status", "PENDING"), "legalName": k.get("legal_name"), "dateOfBirth": k.get("date_of_birth"),
            "countryCode": k.get("country_code"), "verificationProvider": k.get("verification_provider"),
            "verificationReference": k.get("verification_reference"), "submittedAt": k.get("submitted_at"), "approvedAt": k.get("approved_at")}


async def submit_kyc(uid: str, body: KycSubmit) -> dict:
    cur = await db.user_kyc_profiles.find_one({"user_id": uid})
    if cur and cur.get("status") == "APPROVED":
        raise ValueError("KYC already approved")
    await db.user_kyc_profiles.update_one({"user_id": uid}, {"$set": {"legal_name": body.legalName, "date_of_birth": body.dateOfBirth,
                                          "country_code": body.countryCode, "status": "IN_REVIEW", "submitted_at": now_ms()},
                                          "$setOnInsert": {"created_at": now_ms()}}, upsert=True)
    return await get_kyc(uid)


async def set_kyc_status(uid: str, status: str, provider: str = "MANUAL", reference: str = None) -> dict:
    upd = {"status": status, "verification_provider": provider, "verification_reference": reference}
    if status == "APPROVED":
        upd["approved_at"] = now_ms()
    await db.user_kyc_profiles.update_one({"user_id": uid}, {"$set": upd}, upsert=True)
    return await get_kyc(uid)
