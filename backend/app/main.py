"""
IBVAP — Intelligent Border Video Analytics Platform
FastAPI Application Entry Point
"""

import asyncio
import time
from typing import Optional
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

import uuid
import jwt
from backend.app.db.session import engine, SessionLocal
from backend.app.db.base import Base
from backend.app.db.migrator import run_database_migrations
from backend.app.api.v1.router import api
from backend.app.core.config import settings, validate_security_configuration
from backend.app.core.logging import setup_logging, get_logger, set_trace_id, get_trace_id
from backend.app.core.metrics import (
    record_http_request,
    record_detection,
    set_active_ws_count,
    generate_metrics_text,
)
from backend.app.models.user import User
from backend.app.models.camera import Camera
from backend.app.core.security import hash_password, decode_access_token
from backend.app.services.live_pipeline import live_manager
from backend.app.services.video_analysis import (
    set_analysis_event_loop,
    register_job_subscriber,
    unregister_job_subscriber,
)

logger = setup_logging(log_level=settings.log_level, json_format=(settings.log_format == "json"))

# Startup time for uptime calculation
_startup_time = time.time()

# Active WebSocket connections for real-time events
_active_connections: list[WebSocket] = []
# Reference to the running event loop, set during startup
_event_loop = None


def get_uptime() -> float:
    return time.time() - _startup_time


def get_ws_connections() -> list[WebSocket]:
    return _active_connections


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup/shutdown lifecycle.

    IMPORTANT: Only ONE yield. Everything before yield = startup.
    Everything after yield = shutdown.
    """
    # ── Fail-Closed Security Configuration Validation (STARTUP) ──
    validate_security_configuration(settings)

    global _event_loop
    _event_loop = asyncio.get_event_loop()
    set_analysis_event_loop(_event_loop)

    # Create and synchronize tables and performance indexes
    migration_summary = run_database_migrations(engine)
    logger.info(
        "Database schema synchronized",
        extra={
            "tables_count": migration_summary["tables_count"],
            "indexes_verified": migration_summary["indexes_verified"],
        }
    )

    # Bootstrap default data
    db = SessionLocal()
    try:
        # Create default operator user
        if not db.query(User).filter(User.username == "operator").first():
            db.add(User(
                username="operator",
                password_hash=hash_password("operator123"),
                role="OPERATOR",
                full_name="Default Operator",
            ))
            db.add(User(
                username="admin",
                password_hash=hash_password("admin123"),
                role="ADMIN",
                full_name="System Admin",
            ))
            db.add(User(
                username="commander",
                password_hash=hash_password("commander123"),
                role="COMMANDER",
                full_name="Border Commander",
            ))
            db.commit()

        # Demo camera auto-creation disabled — only real hardware cameras used
        # (Previously created synthetic cameras have been removed per user request)

        # ── Wire live pipeline events → WebSocket broadcast (STARTUP) ──
        def on_live_event(event):
            try:
                record_detection(event.get("type", "detection"), event.get("camera_id", ""))
            except Exception:
                pass
            if event.get("type") == "incident_created":
                # Incident alerts are delivered durably by the Transactional Outbox Dispatcher
                return
            if _event_loop and _event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    broadcast_event(event["type"], event),
                    _event_loop,
                )
        live_manager.add_event_listener(on_live_event)

        # ── Wire Outbox Dispatcher → WebSocket broadcast (STARTUP) ──
        from backend.app.services.outbox_dispatcher import global_outbox_dispatcher

        def on_outbox_event(payload):
            if _event_loop and _event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    broadcast_event(payload.get("event_type", "incident_created"), payload),
                    _event_loop,
                )

        global_outbox_dispatcher.set_event_broadcaster(on_outbox_event)
        global_outbox_dispatcher.start()

        # ── Start Spool Replay Worker for offline incident recovery (STARTUP) ──
        from backend.app.services.spool_replay import global_spool_replay_worker
        global_spool_replay_worker.start()

        # ── Start Camera Health Persistence Worker (STARTUP - GAP-P0-01 Phase 5) ──
        from backend.app.services.camera_health_worker import global_health_worker
        global_health_worker.start()

        # ── Start live pipelines for active configured cameras (STARTUP) ──
        try:
            cameras = db.query(Camera).filter(
                Camera.stream_url.notlike("demo://%"),
                Camera.stream_url != "",
                Camera.active == True,
            ).all()
            for cam in cameras:
                live_manager.start_camera(
                    camera_id=cam.id,
                    stream_url=cam.stream_url,
                    camera_name=cam.name,
                    bop=cam.bop,
                )
                logger.info(f"Started live pipeline for camera {cam.id}: {cam.stream_url}")
        except Exception as e:
            logger.warning(f"Error auto-starting camera pipelines: {e}")
    finally:
        db.close()

    # ══════ SINGLE YIELD — everything above runs at startup ══════
    yield
    # ══════ everything below runs at shutdown ══════

    # Cleanup
    global_health_worker.stop(timeout=5.0)
    global_spool_replay_worker.stop(timeout=5.0)
    global_outbox_dispatcher.stop(timeout=5.0)
    live_manager.stop_all()
    engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="2.0.0",
    description="Edge-first border video incident intelligence platform — SIH 2026",
    lifespan=lifespan,
)

# CORS
_origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Security headers middleware
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


app.add_middleware(SecurityHeadersMiddleware)


# Observability & Distributed Tracing Middleware
class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract or generate distributed Trace ID
        trace_id = (
            request.headers.get("X-Trace-ID")
            or request.headers.get("X-Request-ID")
            or f"ibvap-{uuid.uuid4().hex[:12]}"
        )
        set_trace_id(trace_id)

        start = time.time()
        path = request.url.path
        method = request.method

        try:
            response = await call_next(request)
            elapsed_sec = time.time() - start
            elapsed_ms = elapsed_sec * 1000.0

            # Inject trace ID and process time in headers
            response.headers["X-Trace-ID"] = trace_id
            response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"

            # Record Prometheus metrics (skip /metrics itself to prevent loop inflation)
            if path != "/metrics":
                record_http_request(
                    method=method,
                    path=path,
                    status=response.status_code,
                    duration_seconds=elapsed_sec,
                )

            # Emit structured JSON log for non-static assets
            if not path.startswith("/assets") and path != "/favicon.ico":
                client_ip = request.client.host if request.client else "unknown"
                logger.info(
                    f"{method} {path} HTTP/{request.scope.get('http_version', '1.1')} {response.status_code} ({elapsed_ms:.1f}ms)",
                    extra={
                        "trace_id": trace_id,
                        "method": method,
                        "path": path,
                        "status_code": response.status_code,
                        "duration_ms": round(elapsed_ms, 2),
                        "client_ip": client_ip,
                    },
                )

            return response
        except Exception as exc:
            elapsed_sec = time.time() - start
            elapsed_ms = elapsed_sec * 1000.0
            record_http_request(method=method, path=path, status=500, duration_seconds=elapsed_sec)
            logger.error(
                f"Unhandled server error on {method} {path}: {str(exc)}",
                exc_info=True,
                extra={
                    "trace_id": trace_id,
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": round(elapsed_ms, 2),
                },
            )
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error. Please provide the trace_id to system administrators.",
                    "trace_id": trace_id,
                },
                headers={
                    "X-Trace-ID": trace_id,
                    "X-Process-Time-Ms": f"{elapsed_ms:.1f}",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                },
            )


app.add_middleware(ObservabilityMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """
    Fail-closed global exception handler preventing information disclosure.
    Masks database connection strings, stack traces, and internal errors.
    Returns sanitized JSON payload containing a distributed trace ID.
    """
    from fastapi import HTTPException
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.responses import JSONResponse

    if isinstance(exc, (HTTPException, StarletteHTTPException)):
        from fastapi.exception_handlers import http_exception_handler
        return await http_exception_handler(request, exc)

    trace_id = get_trace_id() or f"ibvap-{uuid.uuid4().hex[:12]}"
    logger.error(
        f"Unhandled exception caught by global handler on {request.method} {request.url.path}: {str(exc)}",
        exc_info=True,
        extra={"trace_id": trace_id, "path": request.url.path, "method": request.method},
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error. Please provide the trace_id to system administrators.",
            "trace_id": trace_id,
        },
        headers={
            "X-Trace-ID": trace_id,
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


def _is_test_environment() -> bool:
    """Return True only if running within an authorized automated test environment."""
    import sys, os
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return True
    if os.environ.get("TESTING", "").lower() in ("1", "true", "yes"):
        return True
    return False


# Prometheus metrics exposition endpoint
@app.get("/metrics", include_in_schema=False)
def prometheus_metrics(request: Request):
    """
    Expose RFC-compliant Prometheus/OpenMetrics exposition format (0.0.4)
    telemetry counters, gauges, and latency histograms for Prometheus scrapers.
    Restricts external network access: requires management network (127.0.0.1/::1)
    or authenticated bearer token.
    """
    is_prod = settings.environment.lower() in ("production", "prod")
    client_ip = request.client.host if request.client else "unknown"

    # In production, testclient is NEVER trusted; only explicit loopback IP literals are allowed.
    # In non-production environments, testclient is admitted strictly when running in an authorized test runner.
    if is_prod:
        is_management = client_ip in ("127.0.0.1", "::1")
    else:
        allowed_ips = {"127.0.0.1", "::1", "localhost"}
        if _is_test_environment():
            allowed_ips.add("testclient")
        is_management = client_ip in allowed_ips

    if not is_management:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            from fastapi import HTTPException, status
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Metrics exposition requires management network or valid bearer token.",
            )
        token = auth_header.split(" ", 1)[1]
        decode_access_token(token)

    from fastapi.responses import Response
    return Response(
        content=generate_metrics_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


# API routes
app.include_router(api, prefix="/api/v1")

@app.get("/")
def root(request: Request):
    """API root or SPA entry based on client accept header."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept and _frontend_dist.is_dir():
        from fastapi.responses import FileResponse
        return FileResponse(_frontend_dist / "index.html")
    return {
        "name": settings.app_name,
        "version": "2.0.0",
        "docs": "/docs",
        "status": "operational",
    }


# Initialize evidence directory
_clips_dir = Path(settings.evidence_dir) / "clips"
_clips_dir.mkdir(parents=True, exist_ok=True)
# Static mount removed per Gate 2 security hardening (P0-03).
# All evidence access is strictly routed through authenticated /api/v1/evidence/vault/...

# Serve frontend static files
_frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    from fastapi.responses import FileResponse
    from fastapi import HTTPException

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Never swallow API routes, metrics, or protected data routes with SPA index.html
        if full_path.startswith("api/") or full_path == "api" or full_path.startswith("ws/") or full_path == "metrics" or full_path.startswith("data/") or full_path == "data":
            raise HTTPException(status_code=404, detail=f"Endpoint not found: /{full_path}")

        # Strict path traversal prevention
        try:
            dist_root = _frontend_dist.resolve()
            target_path = (_frontend_dist / full_path).resolve()
            if not str(target_path).startswith(str(dist_root)):
                raise HTTPException(status_code=403, detail="Access denied: Path traversal detected")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid path")

        if target_path.is_file():
            return FileResponse(target_path)

        # Do not serve index.html for missing files with known system/code extensions
        last_segment = full_path.split("/")[-1]
        if "." in last_segment or full_path.startswith("etc/") or full_path.startswith("var/"):
            raise HTTPException(status_code=404, detail=f"Resource not found: /{full_path}")

        return FileResponse(_frontend_dist / "index.html")


async def _authenticate_websocket(
    websocket: WebSocket,
    required_permission: str = "read",
    expected_scope: str = "",
) -> Optional[dict]:
    """
    Authenticate and authorize WebSocket connections fail-closed.
    Validates token from query parameters or Authorization header.
    Enforces active user state in DB, checks role capabilities, and verifies resource scope.
    Closes with 4001 (Unauthorized) or 4003 (Forbidden) if invalid.
    """
    token = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()

    if not token:
        await websocket.close(code=4001, reason="Authentication required")
        return None

    try:
        from backend.app.core.security import verify_streaming_ticket
        payload = verify_streaming_ticket(token, expected_scope=expected_scope)
    except Exception:
        try:
            payload = decode_access_token(token)
        except jwt.ExpiredSignatureError:
            await websocket.close(code=4001, reason="Expired token")
            return None
        except Exception:
            await websocket.close(code=4001, reason="Invalid token")
            return None

    role = payload.get("role", "OPERATOR")
    db = SessionLocal()
    try:
        from backend.app.models.user import User
        from backend.app.core.security import role_has_permission
        username = payload.get("sub", "")
        db_user = db.query(User).filter(User.username == username).first()
        if db_user:
            if not db_user.active:
                await websocket.close(code=4001, reason="User account is inactive")
                return None
            role = db_user.role
    finally:
        db.close()

    if not role_has_permission(role, required_permission):
        await websocket.close(code=4003, reason=f"Forbidden: Missing required capability '{required_permission}'")
        return None

    return {"sub": username, "role": role}


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """WebSocket endpoint for real-time events. Requires valid JWT token with read capability."""
    user_data = await _authenticate_websocket(websocket, required_permission="read", expected_scope="ws:events")
    if not user_data:
        return

    await websocket.accept()
    _active_connections.append(websocket)
    set_active_ws_count(len(_active_connections))
    try:
        while True:
            # Keep connection alive; client can send pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        if websocket in _active_connections:
            _active_connections.remove(websocket)
            set_active_ws_count(len(_active_connections))
    except Exception:
        if websocket in _active_connections:
            _active_connections.remove(websocket)
            set_active_ws_count(len(_active_connections))


@app.websocket("/ws/analysis/{job_id}")
async def websocket_analysis_job(websocket: WebSocket, job_id: int):
    """
    Real-time WebSocket connection streaming progress, detections,
    and incidents for a specific computer vision analysis job.
    Requires valid JWT token with read capability.
    """
    user_data = await _authenticate_websocket(websocket, required_permission="read", expected_scope=f"ws:analysis:{job_id}")
    if not user_data:
        return

    await websocket.accept()
    register_job_subscriber(job_id, websocket)

    # Immediately push current job status upon connection
    db = SessionLocal()
    try:
        from backend.app.models.analysis_job import AnalysisJob
        job = db.get(AnalysisJob, job_id)
        if job:
            await websocket.send_json({
                "event": "job_progress",
                "job_id": job.id,
                "status": job.status,
                "progress_percent": job.progress_percent,
                "processed_frames": job.processed_frames,
                "total_frames": job.total_frames,
                "detections_count": job.detections_count,
                "incidents_count": job.incidents_count,
                "fps": job.fps,
            })
    finally:
        db.close()

    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        unregister_job_subscriber(job_id, websocket)


@app.websocket("/ws/live/{camera_id}")
async def websocket_live_stream(websocket: WebSocket, camera_id: int):
    """
    High-performance binary WebSocket live video stream.
    Streams JPEG frames directly as binary packets, completely bypassing browser
    HTTP/1.1 6-connection limits for unlimited concurrent camera grids.
    Requires valid JWT token with read capability.
    """
    user_data = await _authenticate_websocket(websocket, required_permission="read", expected_scope=f"ws:live:{camera_id}")
    if not user_data:
        return

    await websocket.accept()
    try:
        import cv2
        from concurrent.futures import ThreadPoolExecutor
        from backend.app.models.camera import Camera
        from backend.app.api.v1.endpoints.cameras import stream_manager, get_cached_offline_jpeg

        db = SessionLocal()
        cam = db.get(Camera, camera_id)
        db.close()

        if not cam:
            await websocket.close(code=1008, reason="Camera not found")
            return

        url = cam.stream_url or ""
        target_fps = max(8, min(20, cam.fps or 12))
        interval = 1.0 / target_fps

        _WS_JPEG_QUALITY = 55  # lower quality = faster encode + smaller payload
        _WS_MAX_WIDTH = 640    # downscale large frames for streaming speed
        _encode_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ws-enc")

        def _encode_frame(frame):
            """Downscale + JPEG encode in worker thread (never blocks async loop)."""
            h, w = frame.shape[:2]
            if w > _WS_MAX_WIDTH:
                scale = _WS_MAX_WIDTH / w
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                                   interpolation=cv2.INTER_AREA)
            ret, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _WS_JPEG_QUALITY])
            return buf.tobytes() if ret else None

        _prev_frame_id = None  # dedup identical ring-buffer reads
        _cached_jpeg = None
        _loop_iter = 0

        while True:
            _loop_iter += 1
            if _loop_iter % 20 == 0:
                # Refresh camera active/status state from DB
                db_re = SessionLocal()
                try:
                    c_fresh = db_re.get(Camera, camera_id)
                    if c_fresh:
                        cam.active = c_fresh.active
                        cam.status = c_fresh.status
                        cam.stream_url = c_fresh.stream_url
                        url = c_fresh.stream_url or ""
                except Exception:
                    pass
                finally:
                    db_re.close()

            if not cam.active or cam.status == "OFFLINE" or url.startswith("demo://") or not url:
                offline_bytes = get_cached_offline_jpeg(cam)
                await websocket.send_bytes(offline_bytes)
                await asyncio.sleep(1.5)
                continue

            # Fast path: grab latest frame from live pipeline ring buffer
            from backend.app.services.live_pipeline import live_manager
            raw = live_manager.get_latest_frame(camera_id)
            if raw is None:
                raw = stream_manager.get_frame(url)

            if raw is None:
                offline_bytes = get_cached_offline_jpeg(cam)
                await websocket.send_bytes(offline_bytes)
                await asyncio.sleep(0.5)
                continue

            # Downscale & encode frame
            loop = asyncio.get_event_loop()
            _cached_jpeg = await loop.run_in_executor(_encode_pool, _encode_frame, raw)

            if _cached_jpeg:
                await websocket.send_bytes(_cached_jpeg)

            await asyncio.sleep(interval)

        _encode_pool.shutdown(wait=False)
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception:
        pass


async def broadcast_event(event_type: str, payload: dict):
    """Broadcast an event to all connected WebSocket clients."""
    message = {"type": event_type, "data": payload}
    disconnected = []
    for ws in list(_active_connections):
        try:
            await ws.send_json(message)
        except Exception:
            disconnected.append(ws)
    for ws in disconnected:
        try:
            _active_connections.remove(ws)
        except ValueError:
            pass
