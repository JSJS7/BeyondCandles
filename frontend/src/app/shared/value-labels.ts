// Short labels that change how a value may be read, with what each one means. One list for
// every page (M38): a page names the labels its cells can carry and may add what a label
// means in its own context, but the meaning itself is written only here.

export const VALUE_LABEL_MEANINGS = {
  Basic: 'Basic EPS, because no diluted EPS is reported.',
  Calculated: 'Calculated from values reported in one filing, not one reported figure.',
  'Continuing ops':
    'Operating cash flow of continuing operations, excluding discontinued operations.',
  'Split-adjusted': 'Reported EPS restated for stock splits after the filing.',
  'Series break': "The company's yearly series changes definition here, or a value changes sign.",
  'Spin-off':
    'The price no longer includes a business the company spun off after the period, while ' +
    "the period's results still do, so P/E, P/B and P/S can read too low until the next " +
    'annual report.',
  'Exchangeable units':
    'Part of the owners hold units exchangeable into the listed shares (Blackstone, KKR until ' +
    '2022, Charter, Simon Property, Broadcom in 2017). P/S counts the market value of shares ' +
    "and units, because revenue is the whole group's; P/B counts only the listed shares, " +
    'because equity is theirs. P/E is not affected.',
} as const;

export type ValueLabel = keyof typeof VALUE_LABEL_MEANINGS;

export interface LabelGuideEntry {
  label: ValueLabel;
  text: string;
}

/**
 * The reading guide entries of a page: its labels in its own order, each with the shared
 * meaning and, where the page gives one, a sentence about the label in that page.
 */
export function labelGuide(
  labels: readonly ValueLabel[],
  additions: Partial<Record<ValueLabel, string>> = {},
): LabelGuideEntry[] {
  return labels.map((label) => {
    const meaning = VALUE_LABEL_MEANINGS[label];
    const addition = additions[label];
    if (addition === undefined) {
      return { label, text: meaning };
    }
    return { label, text: `${meaning} ${addition}` };
  });
}
