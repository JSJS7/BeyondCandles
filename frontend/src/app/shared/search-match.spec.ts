import { CONTAINS, EXACT_TICKER, matchRank, NAME_WORD_START, TICKER_START } from './search-match';

describe('Search match rank', () => {
  it('ranks an exact ticker, a ticker start, a name word start and any other part', () => {
    expect(matchRank('T', 'AT&T Inc.', 't')).toBe(EXACT_TICKER);
    expect(matchRank('TGT', 'Target Corporation', 't')).toBe(TICKER_START);
    // "The Boeing Company" has a word starting with "t".
    expect(matchRank('BA', 'The Boeing Company', 't')).toBe(NAME_WORD_START);
    expect(matchRank('MSFT', 'Microsoft Corporation', 'soft')).toBe(CONTAINS);
    expect(matchRank('XOM', 'Exxon Mobil Corporation', 'apple')).toBeNull();
  });

  it('expects a lower-case query and ignores the case of ticker and name', () => {
    expect(matchRank('AAPL', 'Apple Inc.', 'aapl')).toBe(EXACT_TICKER);
    expect(matchRank('AAPL', 'Apple Inc.', 'apple')).toBe(NAME_WORD_START);
  });
});
