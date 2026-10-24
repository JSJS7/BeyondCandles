"""Validate source decimals and daily-price rows before database import."""

from decimal import Decimal, InvalidOperation, localcontext

FIELDS = ["ticker", "date", "open", "high", "low", "close", "adjusted_close", "volume"]
PRICE_COLUMNS = ("Open", "High", "Low", "Close", "Adj Close")
TOLERANCE = Decimal("0.000002")


def number(value, label):
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"Non-finite {label}")
    return result


def validate_price(row):
    values = [number(row[column], column) for column in PRICE_COLUMNS]
    with localcontext() as context:
        context.prec = 50
        for value in values:
            if value <= 0 or value >= Decimal("1e14"):
                raise ValueError("Prices must be positive and fit NUMERIC(32,18)")
            if value != value.quantize(Decimal("1e-18")):
                raise ValueError("Prices must be positive and fit NUMERIC(32,18)")

    open_price, high, low, close, adjusted_close = values
    highest_raw_price = max(open_price, low, close)
    lowest_raw_price = min(open_price, high, close)
    if high + TOLERANCE < highest_raw_price or low - TOLERANCE > lowest_raw_price:
        raise ValueError("Invalid OHLC envelope")
    volume = number(row["Volume"], "Volume")
    if (
        volume < 0
        or volume != volume.to_integral_value()
        or volume > 9223372036854775807
    ):
        raise ValueError("Volume must be a nonnegative BIGINT")
    return [str(v) for v in values] + [str(int(volume))]
