export default defineNuxtRouteMiddleware(async (to) => {
  if (import.meta.server) return
  const auth = useAuth()
  await auth.load()
  const isLogin = to.path === '/login'
  if (!auth.user.value && !isLogin) return navigateTo({ path: '/login', query: { redirect: to.fullPath } })
  if (auth.user.value && isLogin) return navigateTo('/')
})
