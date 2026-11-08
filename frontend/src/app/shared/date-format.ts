const MONTH_NAMES = [
  'Jan',
  'Feb',
  'Mar',
  'Apr',
  'May',
  'Jun',
  'Jul',
  'Aug',
  'Sep',
  'Oct',
  'Nov',
  'Dec',
];

/**
 * An ISO date (2024-12-20) as the pages show it: "20 Dec 2024". Built from the parts of the
 * text itself, so no time zone can move the day.
 */
export function formatDate(isoDate: string): string {
  const [year, month, day] = isoDate.split('-');
  const monthName = MONTH_NAMES[Number(month) - 1];
  return `${Number(day)} ${monthName} ${year}`;
}
