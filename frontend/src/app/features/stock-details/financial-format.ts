// Financial values arrive as decimal strings. They are formatted with string and
// BigInt arithmetic so that no digit is lost to floating-point conversion.

interface DecimalParts {
  negative: boolean;
  integer: string;
  fraction: string;
}

function decimalParts(value: string): DecimalParts {
  const negative = value.startsWith('-');
  const unsigned = negative ? value.slice(1) : value;
  const [integer, fraction = ''] = unsigned.split('.');
  return { negative, integer: integer || '0', fraction };
}

function groupThousands(integer: string): string {
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/**
 * Round a decimal string to a fixed number of decimal places, half away from zero.
 * The result always has exactly `places` decimals; a rounded zero has no minus sign.
 */
export function roundDecimal(value: string, places: number): string {
  const { negative, integer, fraction } = decimalParts(value);
  const keptFraction = fraction.padEnd(places, '0').slice(0, places);
  const firstDroppedDigit = fraction.charAt(places);

  let scaled = BigInt(integer + keptFraction);
  if (firstDroppedDigit >= '5') {
    scaled += 1n;
  }

  const digits = scaled.toString().padStart(places + 1, '0');
  const roundedInteger = digits.slice(0, digits.length - places);
  const roundedFraction = digits.slice(digits.length - places);
  const sign = negative && scaled !== 0n ? '-' : '';
  return places > 0 ? `${sign}${roundedInteger}.${roundedFraction}` : `${sign}${roundedInteger}`;
}

/** Drop trailing zeros beyond `minimumDecimals`; keep every significant digit. */
function trimFraction(fraction: string, minimumDecimals: number): string {
  let trimmed = fraction.replace(/0+$/, '');
  if (trimmed.length < minimumDecimals) {
    trimmed = trimmed.padEnd(minimumDecimals, '0');
  }
  return trimmed;
}

/** The exact value with thousands separators, as reported (e.g. in source details). */
export function formatExact(value: string): string {
  const { negative, integer, fraction } = decimalParts(value);
  const significantFraction = trimFraction(fraction, 0);
  const isZero = /^0*$/.test(integer) && significantFraction === '';
  const sign = negative && !isZero ? '-' : '';
  const grouped = groupThousands(integer.replace(/^0+(?=\d)/, ''));
  return significantFraction ? `${sign}${grouped}.${significantFraction}` : `${sign}${grouped}`;
}

/** The same amount expressed in millions, exact (the decimal point moves six places). */
function inMillions(value: string): string {
  const { negative, integer, fraction } = decimalParts(value);
  const padded = integer.padStart(7, '0');
  const millions = padded.slice(0, padded.length - 6);
  const remainder = padded.slice(padded.length - 6) + fraction;
  return `${negative ? '-' : ''}${millions}.${remainder}`;
}

/**
 * A USD amount in whole millions for the summary table, e.g. 391035000000 → "391,035".
 * A non-zero amount below half a million keeps its exact digits instead of
 * rounding to 0, so that it can never be mistaken for zero.
 */
export function formatMillions(value: string): string {
  const millions = inMillions(value);
  const rounded = roundDecimal(millions, 0);
  const roundsToZero = rounded === '0';
  const isZero = /^-?[0.]*$/.test(value);
  if (roundsToZero && !isZero) {
    return formatExact(millions);
  }
  return formatExact(rounded);
}

/**
 * A per-share amount for the summary table: at least two decimals (6.00), at most
 * four. Split-adjusted EPS can have a repeating fraction (e.g. a third), so the
 * table rounds it; the exact value stays in the source details.
 */
export function formatPerShare(value: string): string {
  const rounded = roundDecimal(value, 4);
  const { negative, integer, fraction } = decimalParts(rounded);
  const sign = negative ? '-' : '';
  return `${sign}${groupThousands(integer)}.${trimFraction(fraction, 2)}`;
}

/** The EDGAR folder of one filing, e.g. CIK 0000320193 + 0000320193-25-000079. */
export function filingIndexUrl(sourceCik: string, accession: string): string | null {
  const cikNumber = sourceCik.replace(/^0+/, '');
  const accessionDigits = accession.replace(/-/g, '');
  const looksLikeEdgarIds = /^\d+$/.test(cikNumber) && /^\d{18}$/.test(accessionDigits);
  if (!looksLikeEdgarIds) {
    return null;
  }
  return `https://www.sec.gov/Archives/edgar/data/${cikNumber}/${accessionDigits}/`;
}
