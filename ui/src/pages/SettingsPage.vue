<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiGet, apiPostForm } from '../api'

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
}

const toast = useToast()

const data = ref<SettingsResponse | null>(null)
const loading = ref(false)
const error = ref('')
const busy = ref('')

const cookieFile = ref<File | null>(null)
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
