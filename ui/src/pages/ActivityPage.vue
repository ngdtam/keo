<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import type { TableColumn } from '@nuxt/ui'
import { apiGet, apiPostJson } from '../api'

interface JobRow {
  id: number
  video_id: string
  artist: string
  title: string
  status: string
  source: string
  error: string | null
  created_at: string
  updated_at: string
  format: string | null
  premium: number | null
}

interface HistoryEvent {
  time: string
  song: string
  kind: string
  detail: string
}

interface ActivityResponse {
  jobs: JobRow[]
  history: HistoryEvent[]
}

const toast = useToast()

const jobs = ref<JobRow[]>([])
const history = ref<HistoryEvent[]>([])
const loading = ref(false)
const error = ref('')
const retryingId = ref<number | null>(null)

const jobColumns: TableColumn<JobRow>[] = [
  { accessorKey: 'status', header: 'Status' },
  { id: 'song', header: 'Song' },
  { accessorKey: 'source', header: 'Source' },
  { accessorKey: 'updated_at', header: 'Updated' },
  { id: 'actions', header: '' },
]

const historyColumns: TableColumn<HistoryEvent>[] = [
  { accessorKey: 'time', header: 'Time' },
  { accessorKey: 'song', header: 'Song' },
  { accessorKey: 'kind', header: 'Event' },
  { accessorKey: 'detail', header: 'Detail' },
]

function statusColor(status: string): 'success' | 'error' | 'primary' | 'neutral' {
  if (status === 'succeeded' || status === 'done') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'running') return 'primary'
  return 'neutral'
}

function isPulling(status: string): boolean {
  return status === 'running' || status === 'queued'
}

let pollTimer: ReturnType<typeof setInterval> | undefined

function syncPolling() {
  const active = jobs.value.some((j) => isPulling(j.status))
  if (active && pollTimer === undefined) {
    pollTimer = setInterval(loadActivity, 3000)
  } else if (!active && pollTimer !== undefined) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

onBeforeUnmount(() => {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
})

async function loadActivity() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiGet<ActivityResponse>('/api/activity')
    jobs.value = res.jobs
    history.value = res.history
    syncPolling()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load activity'
  } finally {
    loading.value = false
  }
}

async function retryJob(id: number) {
  retryingId.value = id
  try {
    await apiPostJson(`/api/jobs/${id}/retry`)
    toast.add({ title: 'Job re-queued', color: 'success' })
    await loadActivity()
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Retry failed',
      color: 'error',
    })
  } finally {
    retryingId.value = null
  }
}

onMounted(loadActivity)
</script>

<template>
  <UDashboardPanel id="activity">
    <template #header>
      <UDashboardNavbar title="Activity">
        <template #right>
          <UButton
            color="neutral"
            variant="outline"
            size="sm"
            icon="i-lucide-refresh-cw"
            :loading="loading"
            @click="loadActivity"
          >
            Refresh
          </UButton>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <UCard>
          <template #header>
            <span class="font-medium text-sm">Download jobs ({{ jobs.length }})</span>
          </template>

          <p v-if="!loading && jobs.length === 0" class="py-6 text-center text-sm text-muted">
            No download jobs yet.
          </p>
          <UTable v-else :data="jobs" :columns="jobColumns" :loading="loading">
            <template #status-cell="{ row }">
              <UBadge :color="statusColor(row.original.status)" variant="subtle" size="xs">
                {{ row.original.status }}
              </UBadge>
            </template>

            <template #song-cell="{ row }">
              <div class="flex flex-col gap-1">
                <span class="font-medium text-sm">
                  {{ row.original.artist }} — {{ row.original.title }}
                </span>
                <UProgress
                  v-if="isPulling(row.original.status)"
                  size="xs"
                  :color="row.original.status === 'running' ? 'primary' : 'neutral'"
                />
                <details v-if="row.original.error" class="text-xs text-error">
                  <summary class="cursor-pointer select-none">Error details</summary>
                  <pre class="mt-1 whitespace-pre-wrap break-all font-mono text-[11px]">{{
                    row.original.error
                  }}</pre>
                </details>
              </div>
            </template>

            <template #actions-cell="{ row }">
              <div class="flex justify-end">
                <UButton
                  v-if="row.original.status === 'failed'"
                  size="xs"
                  color="primary"
                  variant="outline"
                  :loading="retryingId === row.original.id"
                  @click="retryJob(row.original.id)"
                >
                  Retry
                </UButton>
              </div>
            </template>
          </UTable>
        </UCard>

        <UCard>
          <template #header>
            <span class="font-medium text-sm">History log ({{ history.length }})</span>
          </template>

          <p v-if="!loading && history.length === 0" class="py-6 text-center text-sm text-muted">
            No history events yet.
          </p>
          <UTable v-else :data="history" :columns="historyColumns" :loading="loading">
            <template #kind-cell="{ row }">
              <UBadge color="neutral" variant="subtle" size="xs">
                {{ row.original.kind }}
              </UBadge>
            </template>
          </UTable>
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
