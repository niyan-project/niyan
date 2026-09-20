<script setup lang="ts">
const route = useRoute()
const { user } = useAuth()
const navigation = computed(() => {
  const permissions = user.value?.system_permissions || []
  return [
    { label: 'Users', to: '/system/users', icon: 'i-lucide-users', visible: permissions.some(permission => permission.startsWith('users.')) },
    { label: 'Groups', to: '/system/groups', icon: 'i-lucide-folders', visible: permissions.some(permission => permission.startsWith('groups.')) },
    { label: 'Datasets', to: '/system/datasets', icon: 'i-lucide-database', visible: permissions.some(permission => permission.startsWith('datasets.')) }
  ].filter(item => item.visible)
})
</script>

<template>
  <UContainer class="py-8">
    <div class="grid gap-8 md:grid-cols-[17rem_minmax(0,1fr)]">
      <aside>
        <nav aria-label="System settings">
          <p class="mb-2 px-3 text-sm font-semibold text-muted">Administration</p>
          <div class="flex gap-1 overflow-x-auto md:flex-col">
            <UButton v-for="item in navigation" :key="item.to" :to="item.to" :label="item.label" :icon="item.icon" color="neutral" :variant="route.path === item.to ? 'soft' : 'ghost'" class="justify-start" />
          </div>
        </nav>
      </aside>
      <div class="min-w-0"><slot /></div>
    </div>
  </UContainer>
</template>
