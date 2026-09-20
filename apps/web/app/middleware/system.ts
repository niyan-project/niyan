export default defineNuxtRouteMiddleware(() => {
  const { user } = useAuth()
  if (!user.value?.system_permissions?.length) return navigateTo('/')
})
