<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { apiGet, apiPostForm, apiPostJson } from '../api'

interface AuthStatusResponse {
  needs_setup: boolean
  ytm_configured: boolean
}

const router = useRouter()

const checking = ref(true)
const step = ref<1 | 2>(1)

const username = ref('admin')
const password = ref('')
const confirmPassword = ref('')
const remember = ref(true)
const step1Error = ref('')
const step1Loading = ref(false)

const ytmMode = ref<'headers' | 'file'>('headers')
const headersRaw = ref('')
const authFile = ref<File | null>(null)
const hasPremium = ref(false)
const cookieFile = ref<File | null>(null)
const usePremiumQuality = ref(true)
const step2Error = ref('')
const step2Loading = ref(false)

onMounted(async () => {
  try {
    const status = await apiGet<AuthStatusResponse>('/api/auth/status')
    if (!status.needs_setup && status.ytm_configured) {
      await router.replace('/')
      return
    }
    if (!status.needs_setup && !status.ytm_configured) {
      await apiGet('/api/dashboard')
      step.value = 2
    }
  } catch (err) {
    if (window.location.pathname === '/setup') {
      step1Error.value = err instanceof Error ? err.message : 'Failed to check setup status'
    }
  } finally {
    checking.value = false
  }
})

async function createAccount() {
  step1Error.value = ''
  const trimmedUsername = username.value.trim()
  if (!trimmedUsername) {
    step1Error.value = 'Username is required'
    return
  }
  if (!password.value.trim()) {
    step1Error.value = 'Password is required'
    return
  }
  if (password.value !== confirmPassword.value) {
    step1Error.value = 'Passwords do not match'
    return
  }
  step1Loading.value = true
  try {
    await apiPostJson('/api/auth/setup', {
      username: trimmedUsername,
      password: password.value,
      password_confirmation: confirmPassword.value,
      remember: remember.value,
    })
    step.value = 2
  } catch (err) {
    step1Error.value = err instanceof Error ? err.message : 'Failed to create account'
  } finally {
    step1Loading.value = false
  }
}

function onAuthFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  authFile.value = input.files?.[0] ?? null
}

function onCookieFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  cookieFile.value = input.files?.[0] ?? null
}

watch(hasPremium, (checked) => {
  if (!checked) {
    cookieFile.value = null
  }
})

async function saveAndFinish() {
  step2Error.value = ''
  step2Loading.value = true
  try {
    if (ytmMode.value === 'headers' && headersRaw.value.trim()) {
      await apiPostJson('/api/settings/ytm', { headers_raw: headersRaw.value })
    } else if (ytmMode.value === 'file' && authFile.value) {
      await apiPostForm('/api/settings/ytm', { auth_file: authFile.value })
    }

    if (hasPremium.value && cookieFile.value) {
      await apiPostForm('/settings/cookie', { cookie: cookieFile.value })
      if (usePremiumQuality.value) {
        await apiPostForm('/settings', { format: 'opus' })
        await apiPostForm('/settings', { bitrate: 'disable' })
      }
    }

    await router.push('/')
  } catch (err) {
    step2Error.value = err instanceof Error ? err.message : 'Failed to save YouTube Music setup'
  } finally {
    step2Loading.value = false
  }
}

async function skipForNow() {
  await router.push('/')
}
</script>

<template>
  <div class="min-h-screen flex flex-col items-center justify-center p-4">
    <UCard class="w-full max-w-lg">
      <template #header>
        <div class="flex items-center justify-between gap-2">
          <h1 class="text-lg font-semibold">Kéo setup</h1>
          <span class="text-xs text-muted">
            {{ step === 1 ? 'Step 1 of 2 · Create account' : 'Step 2 of 2 · YouTube Music' }}
          </span>
        </div>
      </template>

      <p v-if="checking" class="text-sm text-muted py-4 text-center">
        Checking setup status…
      </p>

      <form v-else-if="step === 1" class="flex flex-col gap-3" @submit.prevent="createAccount">
        <p class="text-xs text-muted">
          Create your sign-in account for Kéo. You can change these credentials later in Settings.
        </p>

        <UFormField label="Username">
          <UInput
            v-model="username"
            name="username"
            autocomplete="username"
            placeholder="admin"
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
            autocomplete="new-password"
            placeholder="Password"
            required
            class="w-full"
          />
        </UFormField>

        <UFormField label="Confirm password">
          <UInput
            v-model="confirmPassword"
            name="password_confirmation"
            type="password"
            autocomplete="new-password"
            placeholder="Confirm password"
            required
            class="w-full"
          />
        </UFormField>

        <UCheckbox v-model="remember" name="remember" label="Remember me" />

        <UAlert v-if="step1Error" color="error" variant="subtle" :description="step1Error" />

        <UButton type="submit" color="primary" :loading="step1Loading" block>
          Create account &amp; continue
        </UButton>
      </form>

      <form v-else class="flex flex-col gap-4" @submit.prevent="saveAndFinish">
        <div class="flex flex-col gap-2">
          <span class="text-sm font-medium">YouTube Music auth (optional)</span>
          <UFieldGroup>
            <UButton
              type="button"
              size="xs"
              :color="ytmMode === 'headers' ? 'primary' : 'neutral'"
              :variant="ytmMode === 'headers' ? 'solid' : 'outline'"
              @click="ytmMode = 'headers'"
            >
              Paste browser headers
            </UButton>
            <UButton
              type="button"
              size="xs"
              :color="ytmMode === 'file' ? 'primary' : 'neutral'"
              :variant="ytmMode === 'file' ? 'solid' : 'outline'"
              @click="ytmMode = 'file'"
            >
              Upload browser.json
            </UButton>
          </UFieldGroup>
        </div>

        <div v-if="ytmMode === 'headers'" class="flex flex-col gap-2">
          <p class="text-xs text-muted leading-relaxed">
            Open music.youtube.com, open DevTools -&gt; Network tab, and click any <code>/browse</code> request while signed in.
            Copy the Request Headers containing <code>cookie</code>, <code>authorization</code>, and <code>x-goog-authuser</code>.
          </p>
          <UTextarea
            v-model="headersRaw"
            :rows="6"
            placeholder="accept: */*&#10;authorization: SAPISIDHASH ...&#10;cookie: ...&#10;x-goog-authuser: 0"
            class="w-full font-mono text-xs"
          />
        </div>

        <div v-else class="flex flex-col gap-2">
          <p class="text-xs text-muted">
            Select an existing <code>browser.json</code> generated by <code>ytmusicapi browser</code>.
          </p>
          <input
            type="file"
            accept=".json,application/json"
            class="text-xs file:mr-2 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:bg-elevated"
            @change="onAuthFileChange"
          />
        </div>

        <div class="border-t border-default pt-3 flex flex-col gap-3">
          <UCheckbox v-model="hasPremium" label="I have YouTube Music Premium" />

          <div v-if="hasPremium" class="flex flex-col gap-3 ps-4 border-s-2 border-default">
            <UFormField label="cookies.txt (optional)">
              <input
                type="file"
                accept=".txt,text/plain"
                class="text-xs file:mr-2 file:py-1.5 file:px-3 file:rounded file:border-0 file:text-xs file:bg-elevated"
                @change="onCookieFileChange"
              />
            </UFormField>

            <UCheckbox
              v-model="usePremiumQuality"
              :disabled="!cookieFile"
              label="Switch download quality to Opus / best bitrate"
            />
          </div>
        </div>

        <UAlert v-if="step2Error" color="error" variant="subtle" :description="step2Error" />

        <div class="flex flex-col gap-2">
          <UButton type="submit" color="primary" :loading="step2Loading" block>
            Save &amp; finish
          </UButton>
          <UButton
            type="button"
            color="neutral"
            variant="ghost"
            :disabled="step2Loading"
            block
            @click="skipForNow"
          >
            Skip for now
          </UButton>
        </div>
      </form>
    </UCard>
  </div>
</template>
