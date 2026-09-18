<script setup lang="ts">
import { NiyanApiError } from '~/composables/useApi'

definePageMeta({ layout: 'auth' })
useHead({ title: 'Sign in' })

const route = useRoute()
const { signIn } = useAuth()
const username = ref('')
const password = ref('')
const loading = ref(false)
const errorMessage = ref('')

async function submit() {
  loading.value = true
  errorMessage.value = ''
  try {
    await signIn(username.value, password.value)
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') && !route.query.redirect.startsWith('//') ? route.query.redirect : '/'
    await navigateTo(redirect)
  } catch (error) {
    errorMessage.value = error instanceof NiyanApiError ? error.message : 'Sign in failed. Try again.'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <UCard>
    <template #header>
      <div>
        <h1 class="text-xl font-medium text-highlighted">Sign in to Niyān</h1>
        <p class="mt-1 text-sm text-muted">Use the account created by your installation administrator.</p>
      </div>
    </template>

    <form class="space-y-4" @submit.prevent="submit">
      <UAlert v-if="errorMessage" color="error" variant="soft" icon="i-lucide-circle-alert" :description="errorMessage" />
      <UFormField label="Username" required>
        <UInput v-model="username" name="username" autocomplete="username" autofocus class="w-full" />
      </UFormField>
      <UFormField label="Password" required>
        <UInput v-model="password" name="password" type="password" autocomplete="current-password" class="w-full" />
      </UFormField>
      <UButton type="submit" block label="Sign in" :loading="loading" :disabled="!username || !password" />
    </form>
  </UCard>
</template>
