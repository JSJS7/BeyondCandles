from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.schemas.stock import Stock


app = FastAPI(
    title="StockEngine API",
    version="0.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/stocks", tags=["stocks"])
def get_stocks() -> list[Stock]:
    return [
        Stock(
            ticker="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
        ),
        Stock(
            ticker="MSFT",
            name="Microsoft Corporation",
            exchange="NASDAQ",
        ),
        Stock(
            ticker="NVDA",
            name="NVIDIA Corporation",
            exchange="NASDAQ",
        ),
        Stock(
            ticker="TSLA",
            name="Tesla Inc.",
            exchange="NASDAQ",
        ),
        Stock(
            ticker="AMD",
            name="Advanced Micro Devices Inc.",
            exchange="NASDAQ",
        ),
    ]