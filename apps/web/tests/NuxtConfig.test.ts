import { describe, expect, it } from 'vitest'
import config, { normalizePublicUrl } from '../nuxt.config'

describe('Nuxt development proxy', () => {
  it.each(['/api', '/admin', '/git'])('preserves the %s mount prefix when forwarding to Django', (prefix) => {
    const proxy = config.nitro?.devProxy?.[prefix]
    expect(proxy).toBeTypeOf('object')
    expect(new URL((proxy as { target: string }).target).pathname).toBe(prefix)
    expect((proxy as { changeOrigin: boolean }).changeOrigin).toBe(false)
  })

  it('exposes one canonical installation URL setting to the generated SPA', () => {
    expect(config.runtimeConfig?.public).toHaveProperty('installationUrl')
  })

  it('rejects deployment URLs with unsupported path prefixes', () => {
    expect(() => normalizePublicUrl('https://example.org/niyan/')).toThrow('without a path')
    expect(normalizePublicUrl('https://niyan.example.org/')).toBe('https://niyan.example.org')
  })
})
