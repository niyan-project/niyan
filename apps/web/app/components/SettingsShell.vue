<script setup lang="ts">
const route = useRoute()
const accountNavigation = [
  { label: 'Profile', to: '/settings/profile', icon: 'i-lucide-user-round' }
]
const securityNavigation = [
  { label: 'Email', to: '/settings/security/email', icon: 'i-lucide-mail' },
  { label: 'Password', to: '/settings/security/password', icon: 'i-lucide-shield-check' },
  { label: 'Access tokens', to: '/settings/security/tokens', icon: 'i-lucide-key-round' }
]
</script>

<template>
  <UContainer class="py-8">
    <div class="grid gap-8 md:grid-cols-[17rem_minmax(0,1fr)]">
      <aside>
        <nav aria-label="Settings">
          <p class="mb-2 px-3 text-sm font-semibold text-muted">Account</p>
          <div class="mb-6 flex gap-1 overflow-x-auto md:flex-col">
            <UButton v-for="item in accountNavigation" :key="item.to" :to="item.to" :label="item.label" :icon="item.icon" color="neutral" :variant="route.path === item.to ? 'soft' : 'ghost'" class="justify-start" />
          </div>
          <p class="mb-2 px-3 text-sm font-semibold text-muted">Security</p>
          <div class="flex gap-1 overflow-x-auto md:flex-col">
            <UButton v-for="item in securityNavigation" :key="item.to" :to="item.to" :label="item.label" :icon="item.icon" color="neutral" :variant="route.path === item.to ? 'soft' : 'ghost'" class="justify-start" />
          </div>
        </nav>
      </aside>
      <div class="min-w-0"><slot /></div>
    </div>
  </UContainer>
</template>
