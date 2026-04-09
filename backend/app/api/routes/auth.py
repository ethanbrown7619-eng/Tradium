from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.database import queries
from app.api.auth import hash_password, verify_password, create_access_token
from app.models.user import UserCreate, UserLogin, UserResponse, TokenResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await queries.get_user_by_email(db, body.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    hashed = hash_password(body.password)
    user = await queries.create_user(db, body.email, hashed)
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email, created_at=user.created_at),
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: UserLogin, db: AsyncSession = Depends(get_db)):
    user = await queries.get_user_by_email(db, body.email)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        user=UserResponse(id=user.id, email=user.email, created_at=user.created_at),
    )


@router.get("/me", response_model=UserResponse)
async def me(user=Depends(queries.get_user_by_id)):
    return user
