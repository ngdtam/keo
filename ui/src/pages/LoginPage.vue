<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiGet, apiPostJson } from '../api'

const router = useRouter()
const username = ref('admin')
const password = ref('')
const remember = ref(true)
const error = ref('')
const loading = ref(false)

onMounted(async () => {
  try {
    const status = await apiGet<{ needs_setup: boolean; ytm_configured: boolean }>('/api/auth/status')
    if (status.needs_setup) {
      await router.replace('/setup')
    }
  } catch {
    // ignore status check errors on login page
  }
})

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await apiPostJson('/api/auth/login', {
      username: username.value,
      password: password.value,
      remember: remember.value,
    })
    await router.push('/')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Sign in failed'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="min-h-screen flex flex-col items-center justify-center p-4">
    <UCard class="w-full max-w-sm">
      <template #header>
        <h1 class="text-lg font-semibold">Kéo</h1>
      </template>

      <form class="flex flex-col gap-3" @submit.prevent="onSubmit">
        <UFormField label="Username">
          <UInput
            v-model="username"
            name="username"
            autocomplete="username"
            placeholder="Username"
            required
            autofocus
            class="w-full"
          />
        </UFormField>
        <UFormField label="Password">
          <UInput
            v-model="password"
            name="password"
            type="password"
            autocomplete="current-password"
            placeholder="Password"
            required
            class="w-full"
          />
        </UFormField>
        <UCheckbox v-model="remember" name="remember" label="Remember me" />
        <UButton type="submit" color="primary" :loading="loading" block>
          Sign in
        </UButton>
        <div class="flex justify-center">
          <a
            href="https://github.com/ngdtam/keo/blob/main/docs/README.md#resetting-your-password"
            target="_blank"
            rel="noreferrer"
            class="text-xs text-muted hover:underline"
          >
            Forgot password?
          </a>
        </div>
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />
      </form>

      <template #footer>
        <p class="text-xs text-muted text-center">
          <a href="https://ytmusicapi.readthedocs.io/" target="_blank" rel="noreferrer" class="hover:underline">ytmusicapi</a>
          ·
          <a href="https://spotdl.readthedocs.io/" target="_blank" rel="noreferrer" class="hover:underline">spotDL</a>
          ·
          <a href="https://mediaarea.net/en/MediaInfo" target="_blank" rel="noreferrer" class="hover:underline">MediaInfo</a>
          ·
          <a href="https://ui.nuxt.com/" target="_blank" rel="noreferrer" class="hover:underline">Nuxt UI</a>
        </p>
      </template>
    </UCard>
  </div>
</template>
