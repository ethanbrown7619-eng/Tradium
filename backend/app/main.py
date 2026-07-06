"""
Tradium — Polymarket Arbitrage Trading Bot
Main FastAPI application entry point.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database.session import engine
from app.database.schema import Base
from app.api.routes import auth, config, trades, markets, wallet, strategies
from app.websocket import ws_endpoint


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="Tradium",
    description="Polymarket Arbitrage Trading Bot",
    version="1.0.0",
    lifespan=lifespan,
)

settings = get_settings()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(auth.router, prefix="/api")
app.include_router(config.router, prefix="/api")
app.include_router(trades.router, prefix="/api")
app.include_router(markets.router, prefix="/api")
app.include_router(wallet.router, prefix="/api")
app.include_router(strategies.router, prefix="/api")

# WebSocket
app.add_api_route("/ws", ws_endpoint, methods=["GET"])


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/api/status")
async def bot_status():
    from app.websocket import manager
    return {
        "ws_connections": manager.connected_count,
    }
