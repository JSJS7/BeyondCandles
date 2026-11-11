import { labelGuide, VALUE_LABEL_MEANINGS } from './value-labels';

describe('value labels', () => {
  it('keeps the page order and adds a page sentence after the shared meaning', () => {
    // M38: the meaning is written once; a page only adds what the label means there.
    const guide = labelGuide(['Series break', 'Basic'], { 'Series break': 'Page sentence.' });

    expect(guide.map((entry) => entry.label)).toEqual(['Series break', 'Basic']);
    expect(guide[0].text).toBe(`${VALUE_LABEL_MEANINGS['Series break']} Page sentence.`);
    expect(guide[1].text).toBe(VALUE_LABEL_MEANINGS.Basic);
  });
});
