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
