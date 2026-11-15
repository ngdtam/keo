<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { TableColumn } from '@nuxt/ui'
import { apiGet, apiPostForm, apiPostJson } from '../api'

interface SongView {
  href: string
  color: 'green' | 'red' | 'yellow'
  label: string
  artist: string
  title: string
  path: string
  size: number | null
  size_human: string
  can_delete: boolean
  ident: string
  cover: string
  kind: 'spotify' | 'video' | 'song'
  quality: string
  details: [string, string][]
}

interface JobLine {
  id: number
  status: string
  error: string | null
  line: string
}

interface Candidate {
  video_id: string
  title: string
  artists: string[]
  album: string | null
}

interface SongDetailResponse {
  song: SongView
  jobs: JobLine[]
  candidates: Candidate[]
}

const route = useRoute()
const router = useRouter()
const toast = useToast()

const kind = computed(() => String(route.params.kind ?? ''))
const ident = computed(() => String(route.params.ident ?? ''))

const data = ref<SongDetailResponse | null>(null)
const error = ref('')
const loading = ref(false)
const busy = ref('')
const retryingId = ref<number | null>(null)

const candidateQuery = ref('')
const linkVideoId = ref('')
const linkArtist = ref('')
const linkTitle = ref('')

const jobColumns: TableColumn<JobLine>[] = [
  { accessorKey: 'status', header: 'Status' },
  { accessorKey: 'line', header: 'Details' },
  { id: 'actions', header: '' },
]

function statusColor(color: SongView['color']): 'success' | 'error' | 'warning' {
  if (color === 'green') return 'success'
  if (color === 'yellow') return 'warning'
  return 'error'
}

function jobBadgeColor(status: string): 'success' | 'error' | 'warning' | 'neutral' {
  if (status === 'done' || status === 'completed') return 'success'
  if (status === 'failed') return 'error'
  if (status === 'running' || status === 'queued') return 'warning'
  return 'neutral'
}

async function loadDetail(searchQ = '') {
  loading.value = true
  error.value = ''
  try {
    const qs = searchQ.trim() ? `?q=${encodeURIComponent(searchQ.trim())}` : ''
    const res = await apiGet<SongDetailResponse>(
      `/api/library/${encodeURIComponent(kind.value)}/${encodeURIComponent(ident.value)}${qs}`,
    )
    data.value = res
    if (!linkArtist.value) linkArtist.value = res.song.artist
    if (!linkTitle.value) linkTitle.value = res.song.title
    if (!candidateQuery.value && res.song.kind === 'song') {
      candidateQuery.value = `${res.song.artist} ${res.song.title}`.trim()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load song'
  } finally {
    loading.value = false
  }
}

async function searchCandidates() {
  await loadDetail(candidateQuery.value)
}

function selectCandidate(candidate: Candidate) {
  linkVideoId.value = candidate.video_id
  linkArtist.value = candidate.artists.join(', ') || linkArtist.value
  linkTitle.value = candidate.title || linkTitle.value
}

async function downloadSong() {
  if (!data.value || data.value.song.kind === 'song') return
  busy.value = 'download'
  try {
    await apiPostJson(
      `/api/library/${data.value.song.kind}/${encodeURIComponent(data.value.song.ident)}/download`,
    )
    toast.add({ title: `Queued ${data.value.song.title}`, color: 'success' })
    await loadDetail()
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Failed to queue download',
      color: 'error',
    })
  } finally {
    busy.value = ''
  }
}

async function deleteSong(scope: 'file' | 'library') {
  if (!data.value) return
  busy.value = `delete-${scope}`
  try {
    await apiPostForm(
      `/library/${data.value.song.kind}/${encodeURIComponent(data.value.song.ident)}/delete`,
      { scope },
    )
    toast.add({
      title: scope === 'file' ? 'File moved to trash' : 'Removed from library',
      color: 'success',
    })
    await router.push('/library')
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Delete failed',
      color: 'error',
    })
  } finally {
    busy.value = ''
  }
}

async function linkSong() {
  if (!data.value) return
  busy.value = 'link'
  try {
    await apiPostForm(`/library/song/${encodeURIComponent(data.value.song.ident)}/link`, {
      video_id: linkVideoId.value.trim(),
      artist: linkArtist.value.trim(),
      title: linkTitle.value.trim(),
    })
    toast.add({ title: 'Song linked', color: 'success' })
    await router.push(`/library/video/${encodeURIComponent(linkVideoId.value.trim())}`)
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Failed to link song',
      color: 'error',
    })
  } finally {
    busy.value = ''
  }
}

async function retryJob(jobId: number) {
  retryingId.value = jobId
  try {
    await apiPostJson(`/api/jobs/${jobId}/retry`)
    toast.add({ title: 'Job retried', color: 'success' })
    await loadDetail()
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Retry failed',
      color: 'error',
    })
  } finally {
    retryingId.value = null
  }
}

watch([kind, ident], () => {
  linkVideoId.value = ''
  linkArtist.value = ''
  linkTitle.value = ''
  candidateQuery.value = ''
  void loadDetail()
})

onMounted(() => {
  void loadDetail()
})
</script>

<template>
  <UDashboardPanel id="song-detail">
    <template #header>
      <UDashboardNavbar :title="data?.song.title || 'Song'">
        <template #right>
          <UButton to="/library" variant="outline" color="neutral" size="sm" icon="i-lucide-arrow-left">
            Back to Library
          </UButton>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <UCard v-if="data">
          <div class="flex flex-col gap-4 sm:flex-row sm:items-center">
            <div class="size-40 shrink-0 overflow-hidden rounded-lg bg-muted">
              <img
                v-if="data.song.cover"
                :src="data.song.cover"
                :alt="data.song.title"
                class="h-full w-full object-cover"
              />
              <div v-else class="flex h-full w-full items-center justify-center text-muted">
                <UIcon name="i-lucide-music" class="size-10" />
              </div>
            </div>

            <div class="flex flex-1 flex-col gap-2">
              <div>
                <h2 class="text-xl font-semibold">{{ data.song.title }}</h2>
                <p class="text-sm text-muted">{{ data.song.artist }}</p>
              </div>

              <div class="flex flex-wrap items-center gap-2">
                <UBadge :color="statusColor(data.song.color)" variant="subtle">
                  {{ data.song.label }}
                </UBadge>
                <UBadge v-if="data.song.quality" color="neutral" variant="subtle">
                  {{ data.song.quality }}
                </UBadge>
                <UBadge v-if="data.song.size_human" color="neutral" variant="subtle">
                  {{ data.song.size_human }}
                </UBadge>
              </div>

              <div class="mt-2 flex flex-wrap items-center gap-2">
                <UButton
                  v-if="data.song.kind === 'video'"
                  :to="`/songs/${data.song.ident}/radio`"
                  variant="outline"
                  color="neutral"
                  size="sm"
                  icon="i-lucide-radio"
                >
                  Radio
                </UButton>
                <UButton
                  v-if="data.song.kind !== 'song' && data.song.color === 'red'"
                  size="sm"
                  icon="i-lucide-download"
                  :loading="busy === 'download'"
                  @click="downloadSong"
                >
                  Download
                </UButton>
                <template v-if="data.song.can_delete">
                  <UButton
                    color="error"
                    variant="outline"
                    size="sm"
                    :loading="busy === 'delete-file'"
                    @click="deleteSong('file')"
                  >
                    Remove file
                  </UButton>
                  <UButton
                    color="error"
                    variant="outline"
                    size="sm"
                    :loading="busy === 'delete-library'"
                    @click="deleteSong('library')"
                  >
                    Remove from library
                  </UButton>
                </template>
              </div>
            </div>
          </div>
        </UCard>

        <div v-if="data" class="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <UCard>
            <template #header>
              <h3 class="font-medium">Audio details</h3>
            </template>

            <dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <template v-for="[label, value] in data.song.details" :key="label">
                <dt class="text-muted">{{ label }}</dt>
                <dd class="break-all">{{ value }}</dd>
              </template>
              <template v-if="data.song.path">
                <dt class="text-muted">Path</dt>
                <dd class="break-all font-mono text-xs">{{ data.song.path }}</dd>
              </template>
              <template v-if="data.song.kind === 'spotify'">
                <dt class="text-muted">Spotify ID</dt>
                <dd class="font-mono text-xs">{{ data.song.ident }}</dd>
              </template>
              <template v-else-if="data.song.kind === 'video'">
                <dt class="text-muted">Video ID</dt>
                <dd class="font-mono text-xs">{{ data.song.ident }}</dd>
              </template>
            </dl>
          </UCard>

          <UCard v-if="data.song.kind === 'song'">
            <template #header>
              <h3 class="font-medium">Link to YouTube Music song</h3>
            </template>

            <div class="flex flex-col gap-3">
              <form class="flex gap-2" @submit.prevent="searchCandidates">
                <UInput
                  v-model="candidateQuery"
                  placeholder="Search YouTube Music..."
                  icon="i-lucide-search"
                  size="sm"
                  class="flex-1"
                />
                <UButton type="submit" size="sm" variant="outline" color="neutral" :loading="loading">
                  Find
                </UButton>
              </form>

              <div v-if="data.candidates.length" class="space-y-1.5">
                <button
                  v-for="cand in data.candidates"
                  :key="cand.video_id"
                  type="button"
                  class="flex w-full items-center justify-between rounded-md border border-default px-3 py-2 text-left text-sm hover:bg-elevated"
                  @click="selectCandidate(cand)"
                >
                  <div class="min-w-0">
                    <p class="truncate font-medium">{{ cand.title }}</p>
                    <p class="truncate text-xs text-muted">
                      {{ cand.artists.join(', ') }}<span v-if="cand.album"> · {{ cand.album }}</span>
                    </p>
                  </div>
                  <span class="ms-2 shrink-0 font-mono text-xs text-muted">{{ cand.video_id }}</span>
                </button>
              </div>

              <form class="flex flex-col gap-2" @submit.prevent="linkSong">
                <UInput v-model="linkVideoId" placeholder="YouTube video ID" size="sm" required />
                <UInput v-model="linkArtist" placeholder="Artist" size="sm" required />
                <UInput v-model="linkTitle" placeholder="Title" size="sm" required />
                <UButton type="submit" size="sm" :loading="busy === 'link'">
                  Link
                </UButton>
              </form>
            </div>
          </UCard>
        </div>

        <UCard v-if="data">
          <template #header>
            <h3 class="font-medium">Download jobs</h3>
          </template>

          <div v-if="data.jobs.length === 0" class="text-sm text-muted">
            No download jobs recorded.
          </div>

          <UTable v-else :data="data.jobs" :columns="jobColumns">
            <template #status-cell="{ row }">
              <UBadge :color="jobBadgeColor(row.original.status)" variant="subtle" size="sm">
                {{ row.original.status }}
              </UBadge>
            </template>

            <template #line-cell="{ row }">
              <div class="space-y-1">
                <p class="text-sm">{{ row.original.line }}</p>
                <details v-if="row.original.error" class="text-xs text-muted">
                  <summary class="cursor-pointer hover:underline">spotDL output</summary>
                  <pre class="mt-1 whitespace-pre-wrap rounded bg-muted p-2 font-mono text-[11px]">{{ row.original.error }}</pre>
                </details>
              </div>
            </template>

            <template #actions-cell="{ row }">
              <UButton
                v-if="row.original.status === 'failed'"
                size="xs"
                variant="outline"
                color="neutral"
                :loading="retryingId === row.original.id"
                @click="retryJob(row.original.id)"
              >
                Retry
              </UButton>
            </template>
          </UTable>
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
