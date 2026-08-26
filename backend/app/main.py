from fastapi import FastAPI, HTTPException
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

STOCKS = [
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


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stocks", tags=["stocks"])
def get_stocks() -> list[Stock]:
    return STOCKS


@app.get("/stocks/{ticker}", tags=["stocks"])
def get_stock(ticker: str) -> Stock:
    normalized_ticker = ticker.upper()

    stock = next(
        (stock for stock in STOCKS if stock.ticker == normalized_ticker),
        None,
    )

    if stock is None:
        raise HTTPException(
            status_code=404,
            detail=f"Stock '{normalized_ticker}' was not found.",
        )

    return stock
