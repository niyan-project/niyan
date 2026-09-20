const PASSWORD_GROUPS = [
  'ABCDEFGHJKLMNPQRSTUVWXYZ',
  'abcdefghijkmnopqrstuvwxyz',
  '23456789',
  '!@#$%^&*()-_=+',
] as const
const PASSWORD_ALPHABET = PASSWORD_GROUPS.join('')

function secureIndex(length: number): number {
  if (!globalThis.crypto?.getRandomValues) throw new Error('Secure password generation is unavailable in this browser.')

  // Rejection sampling avoids the bias introduced by reducing every byte modulo the alphabet length.
  const unbiasedBoundary = 256 - (256 % length)
  const sample = new Uint8Array(1)
  do globalThis.crypto.getRandomValues(sample)
  while (sample[0]! >= unbiasedBoundary)
  return sample[0]! % length
}

export function generateRandomPassword(length = 24): string {
  if (length < PASSWORD_GROUPS.length) throw new RangeError(`Password length must be at least ${PASSWORD_GROUPS.length}.`)

  const characters = PASSWORD_GROUPS.map(group => group[secureIndex(group.length)]!)
  while (characters.length < length) characters.push(PASSWORD_ALPHABET[secureIndex(PASSWORD_ALPHABET.length)]!)

  // A secure shuffle prevents the guaranteed character classes from appearing in predictable positions.
  for (let index = characters.length - 1; index > 0; index -= 1) {
    const swapIndex = secureIndex(index + 1)
    ;[characters[index], characters[swapIndex]] = [characters[swapIndex]!, characters[index]!]
  }
  return characters.join('')
}
