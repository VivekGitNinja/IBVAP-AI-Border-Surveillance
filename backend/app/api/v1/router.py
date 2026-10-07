"""API v1 router — aggregates all endpoint modules."""

from fastapi import APIRouter
from backend.app.api.v1.endpoints import (
    health, auth, cameras, zones, incidents, evidence,
    demo, events, audit, sync, metrics,
    anpr, frs, qrt, media, analysis,
    plates, watchlist, system, map, users, webrtc,
)

api = APIRouter()

# Core
api.include_router(health.router, prefix="/health", tags=["health"])
api.add_api_route("/status", health.system_status, methods=["GET"], tags=["health"])
api.include_router(auth.router, prefix="/auth", tags=["auth"])
api.include_router(users.router, prefix="/users", tags=["users"])
api.include_router(system.router, prefix="/system", tags=["system"])

# Entities
api.include_router(cameras.router, prefix="/cameras", tags=["cameras"])
api.include_router(map.router, prefix="/map", tags=["map"])
api.include_router(zones.router, prefix="/zones", tags=["zones"])
api.include_router(events.router, prefix="/events", tags=["events"])
api.include_router(incidents.router, prefix="/incidents", tags=["incidents"])
api.include_router(evidence.router, prefix="/evidence", tags=["evidence"])
api.include_router(media.router, prefix="/media", tags=["media"])
api.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
api.include_router(plates.router, prefix="/plates", tags=["plates"])
api.include_router(watchlist.router, prefix="/watchlist", tags=["watchlist"])

# C4ISR Specialized Modules
api.include_router(anpr.router, prefix="/anpr", tags=["anpr"])
api.include_router(frs.router, prefix="/frs", tags=["frs"])
api.include_router(qrt.router, prefix="/qrt", tags=["qrt"])

# Operations
api.include_router(audit.router, prefix="/audit", tags=["audit"])
api.include_router(sync.router, prefix="/sync", tags=["sync"])
api.include_router(metrics.router, prefix="/metrics", tags=["metrics"])

# Demo
api.include_router(demo.router, prefix="/demo", tags=["demo"])

# Tactical WebRTC Low-Latency Streaming
api.include_router(webrtc.router, prefix="/webrtc", tags=["webrtc"])
