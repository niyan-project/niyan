<script setup lang="ts">
const { signOut } = useAuth()
const route = useRoute()
const navigation = computed(() => [
  { label: 'Datasets', to: '/', icon: 'i-lucide-database', active: route.path === '/' && route.query.view !== 'groups' },
  { label: 'Groups', to: '/?view=groups', icon: 'i-lucide-users-round', active: route.path === '/' && route.query.view === 'groups' }
])
const userMenu = [
  [{ label: 'Settings', to: '/settings', icon: 'i-lucide-settings' }],
  [{ label: 'Log out', icon: 'i-lucide-log-out', onSelect: () => signOut() }]
]
</script>

<template>
  <div class="min-h-dvh bg-default">
    <UHeader :toggle="false" class="border-b border-default">
      <template #left>
        <NuxtLink to="/" aria-label="Niyān dashboard"><AppLogo /></NuxtLink>
        <nav class="ml-6 hidden items-center gap-1 md:flex" aria-label="Primary navigation">
          <UButton v-for="item in navigation" :key="item.label" :to="item.to" :icon="item.icon" :label="item.label" color="neutral" :variant="item.active ? 'soft' : 'ghost'" />
        </nav>
      </template>
      <template #right>
        <UColorModeButton />
        <UDropdownMenu :items="userMenu" :content="{ align: 'end' }">
          <UButton icon="i-lucide-user-round" aria-label="Open account menu" color="neutral" variant="outline" square class="rounded-full" />
        </UDropdownMenu>
      </template>
    </UHeader>
    <main><slot /></main>
  </div>
</template>
