<script setup lang="ts">
import type { UserProfile } from '~/types/api'

const route = useRoute()
const api = useApi()
const username = computed(() => String(route.params.username))
const { data: profile } = await useAsyncData(`user:${username.value}`, () => api.get<UserProfile>(`/api/v1/auth/users/${encodeURIComponent(username.value)}`), { getCachedData: () => undefined })
useHead({ title: computed(() => profile.value?.display_name || username.value) })
</script>

<template>
  <UContainer class="py-8">
    <UCard v-if="profile" class="mx-auto max-w-2xl">
      <div class="flex items-start gap-4"><div class="flex size-14 shrink-0 items-center justify-center rounded-full bg-elevated text-xl font-medium text-highlighted">{{ profile.display_name.slice(0, 1).toUpperCase() }}</div><div><h1 class="text-2xl font-medium text-highlighted">{{ profile.display_name }}</h1><p class="mt-1 font-mono text-sm text-muted">{{ profile.username }}</p><a v-if="profile.email" class="mt-4 inline-flex items-center gap-2 text-sm text-primary hover:underline" :href="`mailto:${profile.email}`"><UIcon name="i-lucide-mail" class="size-4" />{{ profile.email }}</a><p v-else class="mt-4 text-sm text-muted">No email address provided.</p></div></div>
    </UCard>
  </UContainer>
</template>
