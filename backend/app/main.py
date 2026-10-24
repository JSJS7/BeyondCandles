from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.stocks import router as stocks_router

app = FastAPI(title="BeyondCandles API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://127.0.0.1:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(stocks_router)


@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, error: OperationalError):
    # Driver errors may contain credentials: expose only a fixed public message.
    return JSONResponse(
        status_code=503,
        content={"detail": "Database is temporarily unavailable."},
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
