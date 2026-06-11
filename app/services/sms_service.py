"""SMS Service — MSG91 integration"""

import httpx
from app.core.config import settings


async def send_otp_sms(mobile: str, otp: str) -> bool:
    """Send OTP via MSG91. Returns True on success."""
    if settings.DEBUG:
        print(f"[DEBUG] OTP for {mobile}: {otp}")
        return True

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.msg91.com/api/v5/otp",
                json={
                    "authkey": settings.MSG91_API_KEY,
                    "mobile": f"91{mobile}",
                    "template_id": settings.MSG91_TEMPLATE_ID,
                    "otp": otp,
                },
                timeout=10.0,
            )
            return response.status_code == 200
    except Exception as e:
        print(f"SMS send failed: {e}")
        return False


async def send_sms(mobile: str, message: str) -> bool:
    """Send a generic SMS message."""
    if settings.DEBUG:
        print(f"[DEBUG] SMS to {mobile}: {message}")
        return True

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.msg91.com/api/sendhttp.php",
                params={
                    "authkey": settings.MSG91_API_KEY,
                    "mobiles": f"91{mobile}",
                    "message": message,
                    "sender": settings.MSG91_SENDER_ID,
                    "route": 4,
                },
                timeout=10.0,
            )
            return response.status_code == 200
    except Exception as e:
        print(f"SMS send failed: {e}")
        return False
