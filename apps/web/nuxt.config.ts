import { defineNuxtConfig } from 'nuxt/config'

// https://nuxt.com/docs/api/configuration/nuxt-config
const djangoUrl = (process.env.NIYAN_DJANGO_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

export function normalizePublicUrl(value: string | undefined) {
  if (!value) return ''
  const parsed = new URL(value)
  const isLoopback = ['localhost', '127.0.0.1', '::1'].includes(parsed.hostname)
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.pathname !== '/' || parsed.search || parsed.hash) throw new Error('NIYAN_PUBLIC_URL must be an HTTP or HTTPS origin without a path, query, fragment, or credentials.')
  if (parsed.protocol === 'http:' && !isLoopback) throw new Error('NIYAN_PUBLIC_URL requires HTTPS except for loopback development installations.')
  return parsed.origin
}

const publicUrl = normalizePublicUrl(process.env.NIYAN_PUBLIC_URL)

export default defineNuxtConfig({
  modules: [
    '@nuxt/eslint',
    '@nuxt/ui'
  ],

  ssr: false,

  devtools: {
    enabled: false
  },

  app: {
    buildAssetsDir: '/static/niyan/',
    head: {
      titleTemplate: '%s · Niyān',
      link: [{ rel: 'icon', href: '/static/niyan/favicon.svg?v=2', type: 'image/svg+xml' }]
    }
  },

  css: ['~/assets/css/main.css'],

  runtimeConfig: {
    public: {
      installationUrl: publicUrl
    }
  },

  compatibilityDate: '2026-06-30',

  nitro: {
    devProxy: {
      // Nitro removes the matched mount prefix before proxying. Include it in
      // the target so Django receives the same URL that the browser requested.
      '/api': { target: `${djangoUrl}/api`, changeOrigin: false },
      '/admin': { target: `${djangoUrl}/admin`, changeOrigin: false },
      '/git': { target: `${djangoUrl}/git`, changeOrigin: false }
    }
  },

  typescript: {
    strict: true,
    typeCheck: true
  },

  eslint: {
    config: {
      stylistic: {
        commaDangle: 'never',
        braceStyle: '1tbs'
      }
    }
  },

  fonts: {
    assets: {
      prefix: '/static/niyan/fonts'
    }
  }
})
