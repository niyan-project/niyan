export function useFormatting() {
  const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  const relative = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

  function formatBytes(bytes: number | null | undefined) {
    if (bytes === null || bytes === undefined) return '—'
    if (bytes === 0) return '0 B'
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
    return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
  }

  function formatDate(value: string | null | undefined) {
    return value ? dateTime.format(new Date(value)) : 'Never'
  }

  function formatRelative(value: string) {
    const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
    const ranges: Array<[Intl.RelativeTimeFormatUnit, number]> = [['year', 31_536_000], ['month', 2_592_000], ['day', 86_400], ['hour', 3_600], ['minute', 60]]
    for (const [unit, divisor] of ranges) if (Math.abs(seconds) >= divisor) return relative.format(Math.round(seconds / divisor), unit)
    return relative.format(seconds, 'second')
  }

  return { formatBytes, formatDate, formatRelative }
}
