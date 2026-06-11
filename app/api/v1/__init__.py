from fastapi import APIRouter
from app.api.v1.routes import auth, women, asha, admin

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(women.router)
api_router.include_router(asha.router)
api_router.include_router(admin.router)
