"""Read consolidated instant equity positions from original SEC XBRL instances."""

import io
import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from typing import cast

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
XSI = "{http://www.w3.org/2001/XMLSchema-instance}"
ACCOUNT_ROLES = {
    "CommonStockMember": {"common"},
    "AdditionalPaidInCapitalMember": {"paid_in"},
    "CommonStockIncludingAdditionalPaidInCapitalMember": {"common", "paid_in"},
    "RetainedEarningsMember": {"retained"},
    "AccumulatedOtherComprehensiveIncomeMember": {"aoci"},
    "TreasuryStockCommonMember": {"treasury"},
    "TreasuryStockMember": {"treasury"},
    "PreferredStockMember": {"preferred"},
    "PreferredStockIncludingAdditionalPaidInCapitalMember": {"preferred"},
    "DeferredCompensationShareBasedPaymentsMember": {"deferred_compensation"},
}
SCOPES = {
    "StockholdersEquity": "parent",
    "PartnersCapital": "parent",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": "group",
    "MinorityInterest": "minority",
    "NonredeemableNoncontrollingInterest": "minority",
}


def instant_equity_positions(body, cik, end, *, nci_controls=()):
    """Use whole ownership balances and verified disjoint accounts, never subsidiary or adoption contexts."""
    namespaces = dict(
        value for _, value in ET.iterparse(io.BytesIO(body), events=("start-ns",))
    )
    root = ET.fromstring(body)

    def gaap_name(qname):
        prefix, separator, name = qname.partition(":")
        namespace = namespaces.get(prefix, "")
        if separator and re.fullmatch(r"http://fasb[.]org/us-gaap/[^/]+", namespace):
            return name
        return None

    contexts = {}
    for context in root.findall(XBRLI + "context"):
        entity = context.find(XBRLI + "entity")
        identifier = context.findtext(XBRLI + "entity/" + XBRLI + "identifier", "")
        if identifier.strip().zfill(10) != cik or entity is None:
            continue
        if context.find(XBRLI + "scenario") is not None:
            continue
        if context.find(".//" + XBRLDI + "typedMember") is not None:
            continue
        members = context.findall(".//" + XBRLDI + "explicitMember")
        member = None
        segment = entity.find(XBRLI + "segment")
        if segment is not None:
            if len(segment) != 1 or len(members) != 1:
                continue
            dimension = members[0].get("dimension", "")
            owner = (members[0].text or "").strip()
            if gaap_name(dimension) != "StatementEquityComponentsAxis":
                continue
            member = gaap_name(owner)
            if member is None:
                continue
        period = context.find(XBRLI + "period")
        if period is None or len(period) != 1:
            continue
        if period.findtext(XBRLI + "instant") == str(end):
            contexts[context.attrib["id"]] = member

    usd_units = set()
    for unit in root.findall(XBRLI + "unit"):
        measures = unit.findall(XBRLI + "measure")
        if len(unit) != 1 or len(measures) != 1:
            continue
        measure = (measures[0].text or "").strip()
        prefix, _, name = measure.partition(":")
        if (
            name == "USD"
            and namespaces.get(prefix) == "http://www.xbrl.org/2003/iso4217"
        ):
            usd_units.add(unit.attrib["id"])

    positions = {}
    dimensional_nci = []
    accounts = {}
    unknown_accounts = set()
    for element in root:
        if element.get("contextRef") not in contexts:
            continue
        if (
            element.get("unitRef") not in usd_units
            or element.get(XSI + "nil") == "true"
        ):
            continue
        match = re.fullmatch(r"\{http://fasb[.]org/us-gaap/[^}]+\}(.+)", element.tag)
        if not match or match.group(1) not in SCOPES:
            continue
        tag = match.group(1)
        try:
            value = Decimal((element.text or "").strip())
        except InvalidOperation as exc:
            raise ValueError("Invalid original equity decimal") from exc
        if (
            not value.is_finite()
            or abs(value) >= Decimal("1e28")
            or cast(int, value.as_tuple().exponent) < -10
        ):
            raise ValueError("Original equity exceeds the supported numeric range")
        member = contexts[element.get("contextRef")]
        scope = SCOPES[tag]
        if member == "ParentMember" and scope in ("parent", "group"):
            scope = "parent"
        elif member == "NoncontrollingInterestMember":
            scope = "minority"
        elif member:
            if SCOPES[tag] not in ("parent", "group"):
                continue
            if member in ACCOUNT_ROLES:
                accounts.setdefault(member, set()).add(value)
            else:
                unknown_accounts.add(member)
            continue
        position = {
            "scope": scope,
            "tag": tag,
            "value": value,
            "label": "us-gaap:" + tag,
            "column": f"{end}; consolidated instant context {element.get('contextRef')}"
            + (f"; {member}" if member else ""),
            "layout": "balance",
        }
        if member == "NoncontrollingInterestMember" and SCOPES[tag] == "group":
            # A combined temporary/permanent equity table can use this member for
            # redeemable NCI. It cannot establish a subtraction on its own.
            dimensional_nci.append(position)
        else:
            positions[(scope, tag, value)] = position
    result = list(positions.values())
    if any(position["scope"] == "parent" for position in result):
        return result
    for position in dimensional_nci:
        controls = [
            control
            for control in nci_controls
            if Decimal(control["value"]) == position["value"]
        ]
        if controls:
            result.append(dict(position, scope_control=controls))
    # A complete parent-account closure can identify an otherwise ambiguous
    # NoncontrollingInterestMember as permanent NCI. A redeemable balance outside
    # the total cannot pass this equality (e.g. Copart).
    aoci_details = {
        "AccumulatedGainLossNetCashFlowHedgeParentMember",
        "AccumulatedDefinedBenefitPlansAdjustmentMember",
        "AccumulatedTranslationAdjustmentMember",
        "AccumulatedNetUnrealizedInvestmentGainLossMember",
        "AccumulatedNetGainLossFromCashFlowHedgesIncludingPortionAttributableToNoncontrollingInterestMember",
        "AccumulatedDefinedBenefitPlansAdjustmentNetGainLossIncludingPortionAttributableToNoncontrollingInterestMember",
        "AociIncludingPortionAttributableToNoncontrollingInterestMember",
        "AociDerivativeQualifyingAsHedgeExcludedComponentParentMember",
    }
    if "AccumulatedOtherComprehensiveIncomeMember" in accounts:
        unknown_accounts -= aoci_details
    if accounts and not unknown_accounts:
        roles, controls = set(), []
        # A combined common-stock/APIC account already contains a separately
        # displayed common-stock subtotal. Keep a disjoint set of account roles.
        members = sorted(
            accounts, key=lambda member: (-len(ACCOUNT_ROLES[member]), member)
        )
        for member in members:
            amounts = accounts[member]
            if len(amounts) != 1:
                break
            member_roles = ACCOUNT_ROLES[member]
            if member_roles <= roles:
                continue
            if roles & member_roles:
                break
            roles.update(member_roles)
            controls.append(
                {"column": member, "value": format(next(iter(amounts)), "f")}
            )
        else:
            amount = sum((Decimal(item["value"]) for item in controls), Decimal(0))
            group = [position for position in result if position["scope"] == "group"]
            if len(roles) >= 2 and len(group) == 1:
                if group[0]["value"] == amount:
                    result.append(
                        dict(group[0], scope="parent", scope_control=controls)
                    )
                elif len({position["value"] for position in dimensional_nci}) == 1:
                    minority = dimensional_nci[0]
                    if group[0]["value"] - minority["value"] == amount:
                        result.append(dict(minority, scope_control=controls))
    return result
