import { filingIndexUrl } from './sec-filing';

describe('SEC filing links', () => {
  it('builds the EDGAR filing folder from a CIK and an accession number', () => {
    expect(filingIndexUrl('0000320193', '0000320193-25-000079')).toBe(
      'https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/',
    );
    // Identifiers that are not EDGAR numbers produce no link rather than a broken one.
    expect(filingIndexUrl('0000320193', 'original')).toBeNull();
  });
});
