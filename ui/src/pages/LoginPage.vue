<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiPostJson } from '../api'

const router = useRouter()
const token = ref('')
const error = ref('')
const loading = ref(false)

async function onSubmit() {
  error.value = ''
  loading.value = true
  try {
    await apiPostJson('/api/auth/login', { token: token.value })
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
        <UInput
          v-model="token"
          name="token"
          type="password"
          placeholder="Token"
          required
          autofocus
        />
        <UButton type="submit" color="primary" :loading="loading" block>
          Sign in
        </UButton>
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
