// How a company matches a search text, shared by the top search and the screener, so that
// both find the same company first.

export const EXACT_TICKER = 0;
export const TICKER_START = 1;
export const NAME_WORD_START = 2;
export const CONTAINS = 3;

/**
 * How well a company matches a lower-case query; lower is better, null is no match.
 * An exact ticker comes first, so one-letter tickers such as V or T are not buried
 * under every name that contains the letter.
 */
export function matchRank(ticker: string, name: string, query: string): number | null {
  const lowerTicker = ticker.toLowerCase();
  const lowerName = name.toLowerCase();
  if (lowerTicker === query) {
    return EXACT_TICKER;
  }
  if (lowerTicker.startsWith(query)) {
    return TICKER_START;
  }
  const nameWords = lowerName.split(/[\s,.&()'/-]+/);
  const nameWordStarts =
    lowerName.startsWith(query) || nameWords.some((word) => word.startsWith(query));
  if (nameWordStarts) {
    return NAME_WORD_START;
  }
  if (lowerTicker.includes(query) || lowerName.includes(query)) {
    return CONTAINS;
  }
  return null;
}
