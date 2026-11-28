"""Local-only FastAPI frontend. Does not unlock, modify, or contact brokers."""
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from . import mobile_v3_core as core

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
ASSETS = Path(__file__).with_name("mobile_v3_assets")


@app.middleware("http")
async def local_security(request, call_next):
    if request.headers.get("host") not in ("127.0.0.1:8503", "localhost:8503"):
        return JSONResponse({"error": "Local CCS only"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response


@app.get("/")
def index():
    return FileResponse(ASSETS / "index.html", media_type="text/html")


@app.get("/app.css")
def stylesheet():
    return FileResponse(ASSETS / "app.css", media_type="text/css")


@app.get("/app.js")
def script():
    return FileResponse(ASSETS / "app.js", media_type="application/javascript")


@app.get("/api/home")
def home():
    return core.home()


@app.get("/api/payoff")
def payoff(symbol: str, preset: str = "Covered call"):
    try:
        return core.saved_payoff(symbol, preset)
    except (ValueError, OSError, TypeError, ArithmeticError) as exc:
        raise HTTPException(400, detail=str(exc) if isinstance(exc, ValueError) else "Saved data unavailable")


@app.get("/api/stages")
def stages(avg: float, shares: int, strike: float, lot: int, premium: float, batches: int):
    try:
        return core.stages(avg, shares, strike, lot, premium, batches)
    except (ValueError, ArithmeticError, TypeError) as exc:
        raise HTTPException(400, detail=str(exc))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8503, access_log=False)
