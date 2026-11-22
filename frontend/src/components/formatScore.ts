const percent = new Intl.NumberFormat('pl-PL', { style: 'percent', maximumFractionDigits: 1 })

export function formatScore(value: number) {
  const formatted = percent.format(value)
  return value < 1 && formatted === percent.format(1) ? `< ${formatted}` : formatted
}
