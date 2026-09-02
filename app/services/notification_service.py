# """Notification Service — FCM push notifications"""

# import httpx
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import select

# from app.core.config import settings
# from app.models.models import Alert, Beneficiary, User, FieldWorker


# async def send_fcm_push(fcm_token: str, title: str, body: str, data: dict = None) -> bool:
#     """Send FCM push notification to a single device."""
#     if settings.DEBUG:
#         print(f"[DEBUG] FCM → {fcm_token[:20]}...: {title} | {body}")
#         return True

#     try:
#         async with httpx.AsyncClient() as client:
#             response = await client.post(
#                 "https://fcm.googleapis.com/fcm/send",
#                 headers={
#                     "Authorization": f"key={settings.FCM_SERVER_KEY}",
#                     "Content-Type": "application/json",
#                 },
#                 json={
#                     "to": fcm_token,
#                     "notification": {"title": title, "body": body, "sound": "default"},
#                     "data": data or {},
#                     "priority": "high",
#                 },
#                 timeout=10.0,
#             )
#             return response.status_code == 200
#     except Exception as e:
#         print(f"FCM push failed: {e}")
#         return False


# async def send_alert_to_asha(alert: Alert, beneficiary: Beneficiary, db: AsyncSession) -> bool:
#     """Notify assigned ASHA worker about a new alert via FCM."""
#     if not alert.assigned_to:
#         return False

#     result = await db.execute(
#         select(FieldWorker).where(FieldWorker.id == alert.assigned_to)
#     )
#     worker = result.scalar_one_or_none()
#     if not worker:
#         return False

#     user_result = await db.execute(select(User).where(User.id == worker.user_id))
#     asha_user = user_result.scalar_one_or_none()

#     if not asha_user or not asha_user.fcm_token:
#         return False

#     severity_label = "🔴 आपातकाल" if alert.severity.value == "red" else "🟡 ध्यान दें"

#     return await send_fcm_push(
#         fcm_token=asha_user.fcm_token,
#         title=f"{severity_label} — {beneficiary.name}",
#         body=f"{beneficiary.village or ''} — तुरंत संपर्क करें",
#         data={
#             "alert_id": str(alert.id),
#             "beneficiary_id": str(beneficiary.id),
#             "type": "danger_sign_alert",
#             "deep_link": f"sangwari://alerts/{alert.id}",
#         },
#     )


# async def send_admin_broadcast(title: str, body: str, admin_fcm_tokens: list[str]) -> None:
#     """Send push to multiple admin devices."""
#     for token in admin_fcm_tokens:
#         await send_fcm_push(token, title, body)


"""Notification Service — FCM push notifications (HTTP v1 API via firebase-admin)"""

from firebase_admin import messaging
from firebase_admin.exceptions import FirebaseError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.firebase_app import get_firebase_app
from app.models.models import Alert, Beneficiary, User, FieldWorker

get_firebase_app()  # ensures app is initialized before first send


async def send_fcm_push(
    fcm_token: str,
    title: str,
    body: str,
    data: dict | None = None,
    db: AsyncSession | None = None,
    user_id=None,
) -> bool:
    """Send FCM push notification to a single device via HTTP v1 API."""
    if settings.DEBUG:
        print(f"[DEBUG] FCM → {fcm_token[:20]}...: {title} | {body}")
        return True

    # FCM data payload values must all be strings
    str_data = {k: str(v) for k, v in (data or {}).items()}

    message = messaging.Message(
        token=fcm_token,
        notification=messaging.Notification(title=title, body=body),
        data=str_data,
        android=messaging.AndroidConfig(
            priority="high",
            notification=messaging.AndroidNotification(sound="default"),
        ),
    )

    try:
        messaging.send(message)
        return True
    except messaging.UnregisteredError:
        # Token is dead (app uninstalled, cleared data, etc.) — clean it up
        if db is not None and user_id is not None:
            result = await db.execute(select(User).where(User.id == user_id))
            stale_user = result.scalar_one_or_none()
            if stale_user:
                stale_user.fcm_token = None
                await db.commit()
        return False
    except FirebaseError as e:
        print(f"FCM push failed: {e}")
        return False


async def send_alert_to_asha(alert: Alert, beneficiary: Beneficiary, db: AsyncSession) -> bool:
    """Notify assigned ASHA worker about a new alert via FCM."""
    if not alert.assigned_to:
        return False

    result = await db.execute(
        select(FieldWorker).where(FieldWorker.id == alert.assigned_to)
    )
    worker = result.scalar_one_or_none()
    if not worker:
        return False

    user_result = await db.execute(select(User).where(User.id == worker.user_id))
    asha_user = user_result.scalar_one_or_none()

    if not asha_user or not asha_user.fcm_token:
        return False

    severity_label = "🔴 आपातकाल" if alert.severity.value == "red" else "🟡 ध्यान दें"

    return await send_fcm_push(
        fcm_token=asha_user.fcm_token,
        title=f"{severity_label} — {beneficiary.name}",
        body=f"{beneficiary.village or ''} — तुरंत संपर्क करें",
        data={
            "alert_id": str(alert.id),
            "beneficiary_id": str(beneficiary.id),
            "type": "danger_sign_alert",
            # TODO: point to a real alert-detail route once the ASHA
            # alert screen is built. Mitanin dashboard is still a stub.
            "route": "/mitanindashboard",
        },
        db=db,
        user_id=asha_user.id,
    )


async def send_admin_broadcast(title: str, body: str, admin_fcm_tokens: list[str]) -> None:
    """Send push to multiple admin devices."""
    for token in admin_fcm_tokens:
        await send_fcm_push(token, title, body)