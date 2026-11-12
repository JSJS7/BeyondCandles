import { ScreenerRow } from '../../models/screener';
import { screenerCsv } from './screener-csv';
import { row } from './screener-test-data';

describe('Screener CSV', () => {
  it('writes exact API values, empty fields for no value and a header', () => {
    const company = row('AAPL', { pe: '36.442357', roe: '1.714224' }, 'Technology');
    company.latest = { ...company.latest!, price: '271.859985', shares_basis: 'calculated' };

    const lines = screenerCsv([company], '2025-12-31').split('\r\n');

    expect(lines[0]).toBe(
      'valuation_date,ticker,name,sector,price_date,price,period_start,period_end,' +
        'pe,pb,ps,roe,roa,net_margin,operating_margin,fcf_margin,revenue_growth,eps_growth,' +
        'ocf_to_net_income,equity_to_assets,eps_basis,shares_basis,ocf_scope,' +
        'revenue_series_break,eps_series_break,spin_off_dates,spin_off_fractions,' +
        'exchangeable_units',
    );
    expect(lines[1]).toBe(
      '2025-12-31,AAPL,AAPL Inc.,Technology,2025-12-31,271.859985,2024-01-01,2024-12-31,' +
        '36.442357,,,1.714224,,,,,,,,,diluted,calculated,total,false,false,,,false',
    );
    // The file ends with a line break, so the last line is empty.
    expect(lines).toHaveLength(3);
    expect(lines[2]).toBe('');
  });

  it('writes every spin-off date and fraction, separated by semicolons', () => {
    // Two events in one window do not occur in the data, but the field must not lose one.
    const company = row('GE');
    company.latest = {
      ...company.latest!,
      spin_offs: [
        { event_date: '2023-01-04', separated_fraction: '0.21936' },
        { event_date: '2024-04-02', separated_fraction: '0.202077' },
      ],
    };

    const fields = screenerCsv([company], '2024-06-28').split('\r\n')[1].split(',');

    expect(fields.slice(-3, -1)).toEqual(['2023-01-04;2024-04-02', '0.21936;0.202077']);
  });

  it('writes the exchangeable units mark, so that P/S counting units can be told apart', () => {
    const company = row('BX');
    company.latest = { ...company.latest!, exchangeable_units: true };

    const fields = screenerCsv([company], '2024-12-31').split('\r\n')[1].split(',');

    expect(fields.at(-1)).toBe('true');
  });

  it('quotes a field that holds a comma or a quote', () => {
    const company = row('BRK-B', {}, 'Financial Services');
    company.name = 'Berkshire Hathaway, "B" shares';

    const fields = screenerCsv([company], '2025-12-31').split('\r\n')[1];

    expect(fields).toContain(',"Berkshire Hathaway, ""B"" shares",');
  });

  it('quotes a name whose only special character is a comma', () => {
    // 75 of the 200 company names contain a comma and no quote, e.g. "Advanced Micro Devices, Inc.".
    const company = row('AMD', {}, 'Technology');
    company.name = 'Advanced Micro Devices, Inc.';

    const fields = screenerCsv([company], '2025-12-31').split('\r\n')[1];

    expect(fields).toContain(',"Advanced Micro Devices, Inc.",');
  });

  it('keeps every report column empty for a company with no known report', () => {
    const company: ScreenerRow = { ticker: 'NEW', name: 'New', sector: null, latest: null };

    const fields = screenerCsv([company], '2016-03-31').split('\r\n')[1].split(',');

    expect(fields.slice(0, 3)).toEqual(['2016-03-31', 'NEW', 'New']);
    expect(fields.slice(3).every((field) => field === '')).toBe(true);
  });
});
