"""Convert vendor prices to the share-count basis of normalized EPS.

The vendor scales earlier prices by its own ratio for every corporate event, including
spin-offs, mergers and conversions. Normalized EPS is divided only by reviewed changes
in the number of shares. Ratios of price to per-share values need both on one basis.
"""

from datetime import date
from decimal import Decimal, localcontext
from fractions import Fraction

from app.eps_splits import split_rules

CONVERSION = "vendor_corporate_action_conversion_not_an_ordinary_share_split"

# Through this date the vendor's JCI prices belong to Tyco International, the legal
# predecessor of JCI plc, while the financials follow Johnson Controls Inc. Price and
# financials describe different companies, so no comparable price exists (METHODOLOGY.md).
OTHER_COMPANY_PRICES_UNTIL = {"JCI": date(2016, 9, 2)}


def price_basis_factor(ticker, day, rules):
    """Return the factor that turns a vendor price on this day into the EPS share basis.

    An event affects prices before its date. The vendor divided those prices by its own
    ratio; EPS uses the reviewed share-count ratio, which is 1 for conversions. For an
    action marked vendor_prices_unadjusted_before the vendor divided only prices from that
    day on, so earlier vendor prices still lack its ratio.
    """
    factor = Fraction(1)
    for action in rules["actions"]:
        if action["ticker"] != ticker:
            continue
        unadjusted_before = action.get("vendor_prices_unadjusted_before")
        if unadjusted_before is not None:
            vendor_step_day = date.fromisoformat(unadjusted_before)
            if day < vendor_step_day:
                factor *= Fraction(
                    action["vendor_old_shares"], action["vendor_new_shares"]
                )
        if day >= date.fromisoformat(action["date"]):
            continue
        vendor_ratio = Fraction(
            action["vendor_new_shares"], action["vendor_old_shares"]
        )
        share_ratio = Fraction(action["new_shares"], action["old_shares"])
        factor *= vendor_ratio / share_ratio
    for event in rules["excluded_vendor_events"]:
        if event["ticker"] != ticker or day >= date.fromisoformat(event["date"]):
            continue
        if event["reason"] != CONVERSION:
            # Other exclusions were reviewed only for EPS sources before 2015.
            raise ValueError(f"Vendor price event is not reviewed for prices: {ticker}")
        factor *= Fraction(event["vendor_new_shares"], event["vendor_old_shares"])
    return factor


def price_comparable_to_period(ticker, period_end):
    """False while a period's financials belong to a company other than the price's.

    JCI's prices after the merger are the new company's, but a period ending before it
    still reports the old one, so a ratio joining them compares two different companies.
    """
    other_company_until = OTHER_COMPANY_PRICES_UNTIL.get(ticker)
    return other_company_until is None or period_end > other_company_until


def share_basis_price(ticker, day, price, rules=None):
    """Return the price on the EPS share basis, or None when it is another company's price.

    Accuracy follows the vendor's rounded event ratios (for example 1128/1000), so
    converted prices can differ from historical quotes by about 0.1%.
    """
    other_company_until = OTHER_COMPANY_PRICES_UNTIL.get(ticker)
    if other_company_until is not None and day <= other_company_until:
        return None
    rules = split_rules() if rules is None else rules
    factor = price_basis_factor(ticker, day, rules)
    if factor == 1:
        return price
    with localcontext() as context:
        context.prec = 60
        converted = price * Decimal(factor.numerator) / Decimal(factor.denominator)
        return converted.quantize(Decimal("0.0000000001"))
