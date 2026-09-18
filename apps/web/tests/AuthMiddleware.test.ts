import { mockNuxtImport } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import authMiddleware from '~/middleware/auth.global'

const auth = vi.hoisted(() => ({
  load: vi.fn(),
  user: { value: null as { username: string } | null }
}))
const navigateTo = vi.hoisted(() => vi.fn())

mockNuxtImport('useAuth', () => () => auth)
mockNuxtImport('navigateTo', () => navigateTo)

describe('authentication route middleware', () => {
  beforeEach(() => {
    auth.load.mockReset()
    navigateTo.mockReset()
    auth.user.value = null
  })

  it('sends anonymous visitors to login without losing their requested location', async () => {
    await authMiddleware({ path: '/lab/images', fullPath: '/lab/images?tab=history' } as never, {} as never)

    expect(auth.load).toHaveBeenCalledOnce()
    expect(navigateTo).toHaveBeenCalledWith({ path: '/login', query: { redirect: '/lab/images?tab=history' } })
  })

  it('sends an authenticated visitor away from the login page', async () => {
    auth.user.value = { username: 'researcher' }

    await authMiddleware({ path: '/login', fullPath: '/login' } as never, {} as never)

    expect(navigateTo).toHaveBeenCalledWith('/')
  })
})
