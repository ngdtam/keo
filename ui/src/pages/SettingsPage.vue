<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { apiGet, apiPostForm, apiPostJson } from '../api'

interface SettingChoice {
  name: string
  value: string
  label: string
  selected: boolean
  disabled: boolean
  premium: boolean
}

interface SettingGroup {
  title: string
  choices: SettingChoice[]
}

interface ScheduledTask {
  name: string
  interval_hours: number
  last_run: string | null
}

interface SettingsResponse {
  auth_error: string
  gaps: string[]
  cookie_line: string
  backoff_line: string
  notify_url: string
  media_refresh_url: string
  health_checks: [string, string][]
  last_backup: string | null
  tasks: ScheduledTask[]
  warnings: string[]
  groups: SettingGroup[]
  error: string | null
  recycle_days: number
  config_writable: boolean
  username: string
  api_key: string
}

const toast = useToast()

const data = ref<SettingsResponse | null>(null)
const loading = ref(false)
const error = ref('')
const busy = ref('')

const accountUsername = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const confirmResetKey = ref(false)

const cookieFile = ref<File | null>(null)
const showYtmForm = ref(false)
const ytmMode = ref<'headers' | 'file'>('headers')
const ytmHeadersRaw = ref('')
const ytmAuthFile = ref<File | null>(null)
const notifyUrl = ref('')
const mediaRefreshUrl = ref('')

const spotdlGroups = computed(() =>
  (data.value?.groups ?? []).filter((g) => g.choices[0]?.name !== 'recycle_days'),
)
const recycleGroup = computed(() =>
  (data.value?.groups ?? []).find((g) => g.choices[0]?.name === 'recycle_days'),
)

function healthColor(level: string): 'error' | 'warning' | 'success' | 'neutral' {
  if (level === 'error') return 'error'
  if (level === 'warn' || level === 'warning') return 'warning'
  if (level === 'ok') return 'success'
  return 'neutral'
}

function showError(err: unknown, fallback: string) {
  toast.add({
    title: err instanceof Error ? err.message : fallback,
    color: 'error',
  })
}

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiGet<SettingsResponse>('/api/settings')
    data.value = res
    accountUsername.value = res.username
    confirmResetKey.value = false
    notifyUrl.value = res.notify_url
    mediaRefreshUrl.value = res.media_refresh_url
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load settings'
  } finally {
    loading.value = false
  }
}

async function runPost(key: string, url: string, form?: Record<string, string | File>, msg?: string) {
  busy.value = key
  try {
    await apiPostForm(url, form)
    if (msg) toast.add({ title: msg, color: 'success' })
    await loadSettings()
  } catch (err) {
    showError(err, 'Request failed')
  } finally {
    busy.value = ''
  }
}

function onCookieChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  cookieFile.value = input.files?.[0] ?? null
}

async function uploadCookie() {
  if (!cookieFile.value) return
  await runPost('cookie-upload', '/settings/cookie', { cookie: cookieFile.value }, 'Cookie uploaded')
  cookieFile.value = null
}

function onYtmFileChange(ev: Event) {
  const input = ev.target as HTMLInputElement
  ytmAuthFile.value = input.files?.[0] ?? null
}

watch(ytmMode, () => {
  ytmAuthFile.value = null
})

watch(showYtmForm, (open) => {
  if (!open) {
    ytmAuthFile.value = null
  }
})

async function saveYtmAuth() {
  if (ytmMode.value === 'headers' && !ytmHeadersRaw.value.trim()) return
  if (ytmMode.value === 'file' && !ytmAuthFile.value) return
  busy.value = 'ytm-auth'
  try {
    if (ytmMode.value === 'headers') {
      await apiPostJson('/api/settings/ytm', { headers_raw: ytmHeadersRaw.value })
    } else if (ytmAuthFile.value) {
      await apiPostForm('/api/settings/ytm', { auth_file: ytmAuthFile.value })
    }
    ytmHeadersRaw.value = ''
    ytmAuthFile.value = null
    showYtmForm.value = false
    toast.add({ title: 'YouTube Music connected', color: 'success' })
    await loadSettings()
  } catch (err) {
    showError(err, 'Failed to update YouTube Music auth')
  } finally {
    busy.value = ''
  }
}

async function saveCredentials() {
  const trimmedUsername = accountUsername.value.trim()
  if (!trimmedUsername) {
    toast.add({ title: 'Username is required', color: 'error' })
    return
  }
  if (!newPassword.value.trim()) {
    toast.add({ title: 'Password is required', color: 'error' })
    return
  }
  if (newPassword.value !== confirmPassword.value) {
    toast.add({ title: 'Passwords do not match', color: 'error' })
    return
  }
  busy.value = 'credentials'
  try {
    await apiPostJson('/api/auth/credentials', {
      username: trimmedUsername,
      password: newPassword.value,
      password_confirmation: confirmPassword.value,
    })
    newPassword.value = ''
    confirmPassword.value = ''
    toast.add({ title: 'Credentials updated', color: 'success' })
    await loadSettings()
  } catch (err) {
    showError(err, 'Failed to update credentials')
  } finally {
    busy.value = ''
  }
}

function copyWithTextarea(text: string): boolean {
  const textarea = document.createElement('textarea')
  textarea.value = text
  textarea.setAttribute('readonly', '')
  textarea.style.position = 'fixed'
  textarea.style.left = '-9999px'
  document.body.appendChild(textarea)
  textarea.select()
  try {
    return document.execCommand('copy')
  } finally {
    document.body.removeChild(textarea)
  }
}

async function copyApiKey() {
  const key = data.value?.api_key
  if (!key) return
  try {
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(key)
        toast.add({ title: 'API key copied', color: 'success' })
        return
      } catch {
        // Fall back to execCommand when clipboard API is blocked on non-HTTPS origins
      }
    }
    if (!copyWithTextarea(key)) {
      throw new Error('Failed to copy API key')
    }
    toast.add({ title: 'API key copied', color: 'success' })
  } catch (err) {
    showError(err, 'Failed to copy API key')
  }
}

async function resetApiKey() {
  busy.value = 'api-key-reset'
  try {
    const res = await apiPostJson<{ api_key: string }>('/api/auth/api-key/reset')
    if (data.value) {
      data.value = { ...data.value, api_key: res.api_key }
    }
    confirmResetKey.value = false
    toast.add({ title: 'API key regenerated', color: 'success' })
  } catch (err) {
    showError(err, 'Failed to regenerate API key')
  } finally {
    busy.value = ''
  }
}

onMounted(loadSettings)
</script>

<template>
  <UDashboardPanel id="settings">
    <template #header>
      <UDashboardNavbar title="Settings" />
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />
        <p v-if="loading && !data" class="text-sm text-muted">Loading settings…</p>

        <div v-else-if="data" class="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
          <!-- Left column -->
          <div class="flex flex-col gap-4">
            <UCard>
              <template #header>
                <span class="font-medium text-sm">Security &amp; API</span>
              </template>

              <div class="flex flex-col gap-4">
                <form class="flex flex-col gap-3" @submit.prevent="saveCredentials">
                  <UFormField label="Username">
                    <UInput
                      v-model="accountUsername"
                      name="username"
                      autocomplete="username"
                      required
                      class="w-full"
                    />
                  </UFormField>
                  <UFormField label="New password">
                    <UInput
                      v-model="newPassword"
                      name="new_password"
                      type="password"
                      autocomplete="new-password"
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
                      required
                      class="w-full"
                    />
                  </UFormField>
                  <div>
                    <UButton
                      type="submit"
                      size="sm"
                      color="primary"
                      :disabled="busy !== '' && busy !== 'credentials'"
                      :loading="busy === 'credentials'"
                    >
                      Save credentials
                    </UButton>
                  </div>
                </form>

                <div class="border-t border-default pt-4 flex flex-col gap-3">
                  <UFormField label="API Key">
                    <div class="flex flex-wrap items-center gap-2">
                      <UInput
                        :model-value="data.api_key"
                        readonly
                        class="flex-1 font-mono"
                        @focus="($event.target as HTMLInputElement)?.select()"
                      />
                      <UButton
                        type="button"
                        size="sm"
                        color="neutral"
                        variant="outline"
                        icon="i-lucide-copy"
                        @click="copyApiKey"
                      >
                        Copy
                      </UButton>
                      <UButton
                        v-if="!confirmResetKey"
                        type="button"
                        size="sm"
                        color="error"
                        variant="outline"
                        icon="i-lucide-refresh-cw"
                        :disabled="busy !== ''"
                        @click="confirmResetKey = true"
                      >
                        Regenerate
                      </UButton>
                    </div>
                  </UFormField>

                  <div v-if="confirmResetKey" class="flex flex-wrap items-center gap-2">
                    <span class="text-xs text-muted">
                      Regenerate API key? Existing external clients will stop working.
                    </span>
                    <UButton
                      type="button"
                      size="xs"
                      color="error"
                      :loading="busy === 'api-key-reset'"
                      @click="resetApiKey"
                    >
                      Confirm regenerate
                    </UButton>
                    <UButton
                      type="button"
                      size="xs"
                      color="neutral"
                      variant="ghost"
                      :disabled="busy === 'api-key-reset'"
                      @click="confirmResetKey = false"
                    >
                      Cancel
                    </UButton>
                  </div>

                  <p class="text-xs text-muted">
                    Use header X-Api-Key, Authorization: Bearer, or query ?apikey= to connect external apps.
                  </p>

                  <div class="flex flex-wrap items-center gap-2">
                    <UButton
                      href="/docs"
                      target="_blank"
                      external
                      size="xs"
                      color="neutral"
                      variant="outline"
                    >
                      API Docs
                    </UButton>
                    <UButton
                      href="/openapi.json"
                      target="_blank"
                      external
                      size="xs"
                      color="neutral"
                      variant="outline"
                    >
                      OpenAPI JSON
                    </UButton>
                  </div>
                </div>
              </div>
            </UCard>

            <UCard>
              <template #header>
                <span class="font-medium text-sm">YouTube Music & Cookies</span>
              </template>

              <div class="flex flex-col gap-3">
                <UAlert
                  v-if="data.auth_error"
                  color="error"
                  variant="subtle"
                  :description="data.auth_error"
                />
                <UAlert
                  v-for="gap in data.gaps"
                  :key="gap"
                  color="warning"
                  variant="subtle"
                  :description="gap"
                />
                <UAlert
                  v-for="warn in data.warnings"
                  :key="warn"
                  color="warning"
                  variant="subtle"
                  :description="warn"
                />
                <UAlert
                  v-if="data.backoff_line"
                  color="warning"
                  variant="subtle"
                  :description="data.backoff_line"
                />

                <p class="text-sm text-muted">{{ data.cookie_line }}</p>

                <form class="flex flex-wrap items-center gap-2" @submit.prevent="uploadCookie">
                  <input
                    type="file"
                    accept=".txt,text/plain"
                    class="text-xs file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:bg-elevated"
                    @change="onCookieChange"
                  />
                  <UButton
                    type="submit"
                    size="xs"
                    color="primary"
                    :disabled="!cookieFile || !data.config_writable"
                    :loading="busy === 'cookie-upload'"
                  >
                    Upload cookie
                  </UButton>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === 'cookie-check'"
                    @click="runPost('cookie-check', '/settings/cookie/check', undefined, 'Cookie checked')"
                  >
                    Check cookie now
                  </UButton>
                </form>

                <div class="border-t border-default pt-3 flex flex-col gap-3">
                  <div>
                    <UButton
                      type="button"
                      size="xs"
                      color="neutral"
                      variant="outline"
                      :aria-expanded="showYtmForm"
                      @click="showYtmForm = !showYtmForm"
                    >
                      Update YouTube Music auth
                    </UButton>
                  </div>

                  <form
                    v-if="showYtmForm"
                    class="flex flex-col gap-3"
                    @submit.prevent="saveYtmAuth"
                  >
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

                    <div v-if="ytmMode === 'headers'" class="flex flex-col gap-2">
                      <p class="text-xs text-muted leading-relaxed">
                        Open music.youtube.com, open DevTools -&gt; Network tab, and click any <code>/browse</code> request while signed in.
                        Copy the Request Headers containing <code>cookie</code>, <code>authorization</code>, and <code>x-goog-authuser</code>.
                      </p>
                      <UTextarea
                        v-model="ytmHeadersRaw"
                        :rows="5"
                        placeholder="accept: */*&#10;authorization: SAPISIDHASH ...&#10;cookie: ...&#10;x-goog-authuser: 0"
                        class="w-full font-mono text-xs"
                      />
                    </div>

                    <div v-else class="flex flex-col gap-2">
                      <input
                        type="file"
                        accept=".json,application/json"
                        class="text-xs file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:bg-elevated"
                        @change="onYtmFileChange"
                      />
                    </div>

                    <div class="flex flex-wrap items-center gap-2">
                      <UButton
                        type="submit"
                        size="xs"
                        color="primary"
                        :loading="busy === 'ytm-auth'"
                        :disabled="
                          (ytmMode === 'headers' ? !ytmHeadersRaw.trim() : !ytmAuthFile) ||
                          (busy !== '' && busy !== 'ytm-auth')
                        "
                      >
                        Save YouTube Music auth
                      </UButton>
                      <UButton
                        type="button"
                        size="xs"
                        color="neutral"
                        variant="ghost"
                        :disabled="busy === 'ytm-auth'"
                        @click="showYtmForm = false"
                      >
                        Cancel
                      </UButton>
                    </div>
                  </form>
                </div>
              </div>
            </UCard>

            <UCard>
              <template #header>
                <span class="font-medium text-sm">System</span>
              </template>

              <div class="flex flex-col gap-3">
                <div v-if="data.health_checks.length > 0" class="flex flex-col gap-2">
                  <UAlert
                    v-for="([level, msg], idx) in data.health_checks"
                    :key="idx"
                    :color="healthColor(level)"
                    variant="subtle"
                    :description="msg"
                  />
                </div>

                <p class="text-xs text-muted">
                  Last backup: {{ data.last_backup ?? 'Never' }}
                </p>

                <div class="flex flex-wrap items-center gap-2">
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === 'backup'"
                    @click="runPost('backup', '/settings/backup', undefined, 'Backup created')"
                  >
                    Run backup now
                  </UButton>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === 'updates'"
                    @click="runPost('updates', '/settings/check-updates', undefined, 'Checked for updates')"
                  >
                    Check yt-dlp & spotDL updates
                  </UButton>
                </div>
              </div>
            </UCard>

            <UCard>
              <template #header>
                <span class="font-medium text-sm">Scheduled tasks</span>
              </template>

              <p v-if="data.tasks.length === 0" class="text-xs text-muted">
                No scheduled tasks recorded.
              </p>
              <div v-else class="divide-y divide-default">
                <div
                  v-for="task in data.tasks"
                  :key="task.name"
                  class="py-2 flex items-center justify-between gap-2"
                >
                  <div>
                    <p class="text-sm font-medium">{{ task.name }}</p>
                    <p class="text-xs text-muted">
                      Every {{ task.interval_hours }}h · Last run: {{ task.last_run ?? 'Never' }}
                    </p>
                  </div>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === `task:${task.name}`"
                    @click="
                      runPost(
                        `task:${task.name}`,
                        `/settings/tasks/${encodeURIComponent(task.name)}/run`,
                        undefined,
                        `Ran ${task.name}`,
                      )
                    "
                  >
                    Run now
                  </UButton>
                </div>
              </div>
            </UCard>
          </div>

          <!-- Right column -->
          <div class="flex flex-col gap-4">
            <UCard>
              <template #header>
                <span class="font-medium text-sm">Audio & spotDL</span>
              </template>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div v-for="group in spotdlGroups" :key="group.title" class="flex flex-col gap-1.5">
                  <span class="text-xs font-medium text-muted">{{ group.title }}</span>
                  <UFieldGroup class="flex-wrap">
                    <UButton
                      v-for="choice in group.choices"
                      :key="`${choice.name}:${choice.value}`"
                      size="xs"
                      :color="choice.selected ? 'primary' : 'neutral'"
                      :variant="choice.selected ? 'solid' : 'outline'"
                      :disabled="choice.disabled || !data.config_writable || busy !== ''"
                      @click="
                        runPost(
                          `choice:${choice.name}:${choice.value}`,
                          '/settings',
                          { [choice.name]: choice.value },
                          'Setting saved',
                        )
                      "
                    >
                      {{ choice.label }}
                    </UButton>
                  </UFieldGroup>
                </div>
              </div>
            </UCard>

            <UCard>
              <template #header>
                <span class="font-medium text-sm">Integrations & Recycle bin</span>
              </template>

              <div class="flex flex-col gap-4">
                <div v-if="recycleGroup" class="flex flex-col gap-1.5">
                  <span class="text-xs font-medium text-muted">{{ recycleGroup.title }}</span>
                  <UFieldGroup class="flex-wrap">
                    <UButton
                      v-for="choice in recycleGroup.choices"
                      :key="choice.value"
                      size="xs"
                      :color="choice.selected ? 'primary' : 'neutral'"
                      :variant="choice.selected ? 'solid' : 'outline'"
                      :disabled="choice.disabled || !data.config_writable || busy !== ''"
                      @click="
                        runPost(
                          `recycle:${choice.value}`,
                          '/settings',
                          { recycle_days: choice.value },
                          'Retention saved',
                        )
                      "
                    >
                      {{ choice.label }}
                    </UButton>
                  </UFieldGroup>
                </div>

                <form
                  class="flex flex-col gap-3"
                  @submit.prevent="
                    runPost(
                      'integrations',
                      '/settings/integrations',
                      { notify_url: notifyUrl, media_refresh_url: mediaRefreshUrl },
                      'Integrations saved',
                    )
                  "
                >
                  <UFormField label="Notify webhook URL">
                    <UInput v-model="notifyUrl" placeholder="https://..." class="w-full" />
                  </UFormField>
                  <UFormField label="Media server refresh URL">
                    <UInput v-model="mediaRefreshUrl" placeholder="https://..." class="w-full" />
                  </UFormField>
                  <div>
                    <UButton
                      type="submit"
                      size="sm"
                      color="primary"
                      :disabled="!data.config_writable"
                      :loading="busy === 'integrations'"
                    >
                      Save integrations
                    </UButton>
                  </div>
                </form>
              </div>
            </UCard>
          </div>
        </div>
      </div>
    </template>
  </UDashboardPanel>
</template>
