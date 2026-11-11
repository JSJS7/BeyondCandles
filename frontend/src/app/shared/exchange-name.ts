// The API preserves the vendor's exchange codes; they are translated only for presentation.
const EXCHANGE_NAMES: Record<string, string> = {
  NMS: 'NASDAQ',
  NYQ: 'NYSE',
};

export function exchangeName(code: string): string {
  return EXCHANGE_NAMES[code] ?? code;
}
