<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiGet, apiPostForm, apiPostJson } from '../api'

interface LibraryRow {
  href: string
  color: 'green' | 'red' | 'yellow'
  label: string
  artist: string
  title: string
  album: string
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

interface LibraryResponse {
  rows: LibraryRow[]
  filter: string
  q: string
  sort: string
  artist: string
  stats: string
  total_count: number
  missing_count: number
  upgradable_count: number
  unmatched_count: number
  in_album_count: number
  no_album_count: number
}

interface ArtistTrack {
  video_id: string
  title: string
  artists: string
  album: string
  in_album: boolean
  owned: boolean
  active: boolean
}

interface ArtistCheckResponse {
  found: boolean
  artist: string
  channel_id: string | null
  total: number
  owned_count: number
  missing_count: number
  tracks: ArtistTrack[]
}

const COVER_SIZE_KEY = 'keo_cover_size'
const VIEW_MODE_KEY = 'keo_library_view'
const DEFAULT_COVER_SIZE = 160

function readStoredCoverSize(): number {
  const raw = localStorage.getItem(COVER_SIZE_KEY)
  const parsed = raw ? Number(raw) : NaN
  if (Number.isFinite(parsed) && parsed >= 96 && parsed <= 320) {
    return parsed
  }
  return DEFAULT_COVER_SIZE
}

const route = useRoute()
const router = useRouter()
const toast = useToast()

const data = ref<LibraryResponse | null>(null)
const error = ref('')
const loading = ref(false)
const busy = ref('')
const downloadingKey = ref('')
const selected = ref<string[]>([])

const filter = ref(typeof route.query.filter === 'string' ? route.query.filter : 'all')
const search = ref(typeof route.query.q === 'string' ? route.query.q : '')
const sort = ref(typeof route.query.sort === 'string' ? route.query.sort : 'artist')
const artistFilter = ref(typeof route.query.artist === 'string' ? route.query.artist : '')
const coverSize = ref(readStoredCoverSize())
const viewMode = ref<'grid' | 'list'>(localStorage.getItem(VIEW_MODE_KEY) === 'list' ? 'list' : 'grid')
const checkingAlbumKey = ref('')

const artistCheckOpen = ref(false)
const artistCheckLoading = ref(false)
const artistCheckError = ref('')
const artistCheckQuery = ref('')
const artistCheckData = ref<ArtistCheckResponse | null>(null)
const artistCheckFilter = ref<'all' | 'missing' | 'owned'>('all')
const queueingVideoId = ref('')

const sortItems = [
  { label: 'Artist', value: 'artist' },
  { label: 'Title', value: 'title' },
  { label: 'Size', value: 'size' },
]

const filterButtons = computed(() => [
  { id: 'all', label: `All (${data.value?.total_count ?? 0})` },
  { id: 'missing', label: `Missing (${data.value?.missing_count ?? 0})` },
  { id: 'upgradable', label: `Upgradable (${data.value?.upgradable_count ?? 0})` },
  { id: 'unmatched', label: `Unmatched (${data.value?.unmatched_count ?? 0})` },
  { id: 'in_album', label: `In album (${data.value?.in_album_count ?? 0})` },
  { id: 'no_album', label: `No album (${data.value?.no_album_count ?? 0})` },
])

const filteredArtistTracks = computed(() => {
  const tracks = artistCheckData.value?.tracks ?? []
  if (artistCheckFilter.value === 'missing') return tracks.filter((t) => !t.owned)
  if (artistCheckFilter.value === 'owned') return tracks.filter((t) => t.owned)
  return tracks
})

watch(coverSize, (val) => {
  localStorage.setItem(COVER_SIZE_KEY, String(val))
})

watch(viewMode, (val) => {
  localStorage.setItem(VIEW_MODE_KEY, val)
})

function itemKey(row: Pick<LibraryRow, 'kind' | 'ident'>): string {
  return `${row.kind}:${row.ident}`
}

function isSelected(row: LibraryRow): boolean {
  return selected.value.includes(itemKey(row))
}

function toggleSelected(row: LibraryRow, checked: boolean | 'indeterminate') {
  const key = itemKey(row)
  if (checked === true) {
    if (!selected.value.includes(key)) {
      selected.value = [...selected.value, key]
    }
  } else {
    selected.value = selected.value.filter((item) => item !== key)
  }
}

const allVisibleSelected = computed<boolean | 'indeterminate'>(() => {
  const rows = data.value?.rows ?? []
  if (rows.length === 0) return false
  const count = rows.filter((r) => selected.value.includes(itemKey(r))).length
  if (count === 0) return false
  return count === rows.length ? true : 'indeterminate'
})

function toggleAllVisible(checked: boolean | 'indeterminate') {
  const rows = data.value?.rows ?? []
  const keys = rows.map(itemKey)
  if (checked === true) {
    selected.value = Array.from(new Set([...selected.value, ...keys]))
  } else {
    const keySet = new Set(keys)
    selected.value = selected.value.filter((k) => !keySet.has(k))
  }
}

function statusStripClass(color: LibraryRow['color']): string {
  if (color === 'green') return 'bg-success'
  if (color === 'yellow') return 'bg-warning'
  return 'bg-error'
}

function sourceMeta(kind: LibraryRow['kind']): { label: string; color: 'success' | 'error' | 'neutral' } {
  if (kind === 'spotify') return { label: 'Spotify', color: 'success' }
  if (kind === 'video') return { label: 'YouTube', color: 'error' }
  return { label: 'Local', color: 'neutral' }
}

function showError(err: unknown, fallback: string) {
  const message = err instanceof Error ? err.message : fallback
  toast.add({ title: message, color: 'error' })
}

async function loadLibrary() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (filter.value && filter.value !== 'all') params.set('filter', filter.value)
    if (search.value.trim()) params.set('q', search.value.trim())
    if (sort.value && sort.value !== 'artist') params.set('sort', sort.value)
    if (artistFilter.value.trim()) params.set('artist', artistFilter.value.trim())
    const qs = params.toString()
    data.value = await apiGet<LibraryResponse>(`/api/library${qs ? `?${qs}` : ''}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load library'
  } finally {
    loading.value = false
  }
}

async function syncQueryAndReload() {
  const query: Record<string, string> = {}
  if (filter.value && filter.value !== 'all') query.filter = filter.value
  if (search.value.trim()) query.q = search.value.trim()
  if (sort.value && sort.value !== 'artist') query.sort = sort.value
  if (artistFilter.value.trim()) query.artist = artistFilter.value.trim()
  await router.replace({ query })
  await loadLibrary()
}

function primaryArtistName(artist: string): string {
  const first = artist.split(',')[0]?.trim()
  return first || artist.trim()
}

function filterByArtist(artist: string) {
  artistFilter.value = primaryArtistName(artist)
  void syncQueryAndReload()
}

function clearArtistFilter() {
  artistFilter.value = ''
  void syncQueryAndReload()
}

async function openArtistCheck(artist: string) {
  const target = primaryArtistName(artist)
  if (!target) return
  artistCheckQuery.value = target
  artistCheckOpen.value = true
  artistCheckLoading.value = true
  artistCheckError.value = ''
  artistCheckData.value = null
  artistCheckFilter.value = 'all'
  try {
    const params = new URLSearchParams({ artist: target })
    artistCheckData.value = await apiGet<ArtistCheckResponse>(`/api/library/artist-check?${params}`)
  } catch (err) {
    artistCheckError.value = err instanceof Error ? err.message : 'Failed to check artist releases'
  } finally {
    artistCheckLoading.value = false
  }
}

async function queueArtistTrack(track: ArtistTrack) {
  queueingVideoId.value = track.video_id
  try {
    await apiPostJson('/api/downloads', {
      video_id: track.video_id,
      title: track.title,
      artists: [track.artists],
    })
    track.active = true
    toast.add({ title: `Queued ${track.title}`, color: 'success' })
  } catch (err) {
    showError(err, 'Failed to queue song')
  } finally {
    queueingVideoId.value = ''
  }
}

async function checkRowAlbum(row: LibraryRow) {
  const key = itemKey(row)
  checkingAlbumKey.value = key
  try {
    const res = await apiPostJson<{ found: boolean; album: string }>(
      `/api/library/${row.kind}/${row.ident}/check-album`,
    )
    if (res.found && res.album) {
      row.album = res.album
      toast.add({ title: `Album: ${res.album}`, color: 'success' })
      await loadLibrary()
    } else {
      toast.add({ title: `No album found for ${row.title}`, color: 'neutral' })
    }
  } catch (err) {
    showError(err, 'Album check failed')
  } finally {
    checkingAlbumKey.value = ''
  }
}

function setFilter(next: string) {
  filter.value = next
  void syncQueryAndReload()
}

let searchTimer: ReturnType<typeof setTimeout> | undefined
function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    void syncQueryAndReload()
  }, 250)
}

function onSortChange() {
  void syncQueryAndReload()
}

async function runAction(key: string, url: string, successTitle: string) {
  busy.value = key
  try {
    await apiPostForm(url)
    toast.add({ title: successTitle, color: 'success' })
    await loadLibrary()
  } catch (err) {
    showError(err, 'Action failed')
  } finally {
    busy.value = ''
  }
}

async function downloadRow(row: LibraryRow) {
  if (row.kind === 'song') return
  const key = itemKey(row)
  downloadingKey.value = key
  try {
    await apiPostJson(`/api/library/${row.kind}/${row.ident}/download`)
    toast.add({ title: `Queued ${row.title}`, color: 'success' })
    await loadLibrary()
  } catch (err) {
    showError(err, 'Failed to queue download')
  } finally {
    downloadingKey.value = ''
  }
}

async function runBulk(action: 'redownload' | 'delete') {
  if (selected.value.length === 0) return
  busy.value = `bulk-${action}`
  try {
    await apiPostForm('/library/bulk', {
      action,
      items: selected.value,
    })
    toast.add({
      title: action === 'redownload' ? 'Queued selected items' : 'Deleted selected items',
      color: 'success',
    })
    selected.value = []
    await loadLibrary()
  } catch (err) {
    showError(err, 'Bulk action failed')
  } finally {
    busy.value = ''
  }
}

onMounted(loadLibrary)
</script>

<template>
  <UDashboardPanel id="library">
    <template #header>
      <UDashboardNavbar title="Library">
        <template #trailing>
          <UBadge v-if="data?.stats" color="neutral" variant="subtle">
            {{ data.stats }}
          </UBadge>
        </template>

        <template #right>
          <div class="flex flex-wrap items-center gap-2">
            <UButton
              size="sm"
              variant="outline"
              color="neutral"
              icon="i-lucide-refresh-cw"
              :loading="busy === 'rescan'"
              @click="runAction('rescan', '/library/rescan', 'Library rescanned')"
            >
              Rescan
            </UButton>
            <UButton
              size="sm"
              variant="outline"
              color="neutral"
              icon="i-lucide-folder-pen"
              to="/library/organize/preview"
            >
              Preview rename
            </UButton>
            <UButton
              size="sm"
              variant="outline"
              color="neutral"
              icon="i-lucide-folder-sync"
              :loading="busy === 'organize'"
              @click="runAction('organize', '/library/organize', 'Library organized')"
            >
              Organize
            </UButton>
            <UButton
              size="sm"
              variant="outline"
              color="neutral"
              icon="i-lucide-trash-2"
              to="/library/trash"
            >
              Trash
            </UButton>
            <UButton
              v-if="data && data.missing_count > 0"
              size="sm"
              color="primary"
              icon="i-lucide-download"
              :loading="busy === 'redownload-missing'"
              @click="runAction('redownload-missing', '/library/redownload-missing', 'Queued missing downloads')"
            >
              Re-download missing
            </UButton>
            <UButton
              v-if="data && data.upgradable_count > 0"
              size="sm"
              color="primary"
              variant="soft"
              icon="i-lucide-arrow-up-circle"
              :loading="busy === 'upgrade-all'"
              @click="runAction('upgrade-all', '/library/upgrade-all', 'Queued upgrades')"
            >
              Upgrade all
            </UButton>
          </div>
        </template>
      </UDashboardNavbar>

      <UDashboardToolbar>
        <template #left>
          <UFieldGroup size="sm">
            <UButton
              v-for="btn in filterButtons"
              :key="btn.id"
              :color="filter === btn.id ? 'primary' : 'neutral'"
              :variant="filter === btn.id ? 'solid' : 'outline'"
              @click="setFilter(btn.id)"
            >
              {{ btn.label }}
            </UButton>
          </UFieldGroup>
        </template>

        <template #right>
          <div class="flex flex-wrap items-center gap-3">
            <UInput
              v-model="search"
              placeholder="Search artist, title, or album..."
              icon="i-lucide-search"
              size="sm"
              @update:model-value="onSearchInput"
            />
            <USelect
              v-model="sort"
              :items="sortItems"
              size="sm"
              class="w-28"
              @update:model-value="onSortChange"
            />
            <div v-if="viewMode === 'grid'" class="flex items-center gap-2 w-28" title="Cover size">
              <UIcon name="i-lucide-grid-2x2" class="size-4 text-muted shrink-0" />
              <USlider v-model="coverSize" :min="96" :max="320" :step="8" size="sm" />
            </div>
            <UFieldGroup size="sm">
              <UButton
                icon="i-lucide-layout-grid"
                aria-label="Grid view"
                :color="viewMode === 'grid' ? 'primary' : 'neutral'"
                :variant="viewMode === 'grid' ? 'solid' : 'outline'"
                @click="viewMode = 'grid'"
              >
                Grid
              </UButton>
              <UButton
                icon="i-lucide-list"
                aria-label="List view"
                :color="viewMode === 'list' ? 'primary' : 'neutral'"
                :variant="viewMode === 'list' ? 'solid' : 'outline'"
                @click="viewMode = 'list'"
              >
                List
              </UButton>
            </UFieldGroup>
          </div>
        </template>
      </UDashboardToolbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <div
          v-if="artistFilter"
          class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-default bg-elevated/50 px-3 py-2 text-sm"
        >
          <div class="flex items-center gap-2">
            <UIcon name="i-lucide-user" class="size-4 text-primary" />
            <span>Artist: <strong>{{ artistFilter }}</strong></span>
          </div>
          <div class="flex items-center gap-2">
            <UButton
              size="xs"
              color="primary"
              variant="soft"
              icon="i-lucide-disc-3"
              @click="openArtistCheck(artistFilter)"
            >
              Check artist on YouTube Music
            </UButton>
            <UButton
              size="xs"
              color="neutral"
              variant="ghost"
              icon="i-lucide-x"
              @click="clearArtistFilter"
            >
              Clear
            </UButton>
          </div>
        </div>

        <div
          v-if="data && data.rows.length === 0 && !loading"
          class="py-12 text-center text-sm text-muted"
        >
          No songs match the current filter.
        </div>

        <div
          v-if="data && data.rows.length > 0 && viewMode === 'grid'"
          class="grid gap-4"
          :style="{
            '--cover': `${coverSize}px`,
            gridTemplateColumns: 'repeat(auto-fill, minmax(var(--cover, 160px), 1fr))',
          }"
        >
          <div
            v-for="row in data.rows"
            :key="itemKey(row)"
            class="group relative flex flex-col overflow-hidden rounded-lg border border-default bg-elevated/40"
          >
            <div class="absolute top-2 left-2 z-10 rounded bg-default/80 p-0.5 backdrop-blur-xs">
              <UCheckbox
                :model-value="isSelected(row)"
                :aria-label="`Select ${row.title}`"
                @update:model-value="toggleSelected(row, $event)"
              />
            </div>

            <div class="absolute top-2 right-2 z-10">
              <UTooltip :text="sourceMeta(row.kind).label">
                <UBadge
                  :color="sourceMeta(row.kind).color"
                  variant="subtle"
                  size="xs"
                  :aria-label="sourceMeta(row.kind).label"
                  class="px-1.5 py-1 bg-default/85 backdrop-blur-xs"
                >
                  <svg v-if="row.kind === 'spotify'" viewBox="0 0 24 24" class="size-3.5 shrink-0 fill-current" aria-hidden="true">
                    <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm4.59 14.42a.62.62 0 0 1-.86.2c-2.35-1.44-5.31-1.76-8.8-.96a.62.62 0 1 1-.28-1.22c3.82-.87 7.1-.5 9.74 1.12.3.18.39.56.2.86Zm1.22-2.72a.78.78 0 0 1-1.07.26c-2.69-1.65-6.79-2.13-9.97-1.16a.78.78 0 1 1-.46-1.5c3.63-1.1 8.15-.57 11.24 1.33.37.22.48.7.26 1.07Zm.11-2.83c-3.22-1.91-8.54-2.09-11.62-1.15a.94.94 0 0 1-.54-1.8c3.53-1.07 9.4-.87 13.11 1.34a.94.94 0 0 1-.95 1.61Z" />
                  </svg>
                  <svg v-else-if="row.kind === 'video'" viewBox="0 0 24 24" class="size-3.5 shrink-0 fill-current" aria-hidden="true">
                    <path d="M21.58 7.19a2.75 2.75 0 0 0-1.94-1.95C17.93 4.78 12 4.78 12 4.78s-5.93 0-7.64.46A2.75 2.75 0 0 0 2.42 7.2 28.6 28.6 0 0 0 1.96 12a28.6 28.6 0 0 0 .46 4.81 2.75 2.75 0 0 0 1.94 1.95c1.71.46 7.64.46 7.64.46s5.93 0 7.64-.46a2.75 2.75 0 0 0 1.94-1.95A28.6 28.6 0 0 0 22.04 12a28.6 28.6 0 0 0-.46-4.81ZM9.98 15.02V8.98L15.22 12l-5.24 3.02Z" />
                  </svg>
                  <UIcon v-else name="i-lucide-hard-drive" class="size-3.5 shrink-0" />
                </UBadge>
              </UTooltip>
            </div>

            <RouterLink :to="row.href" class="block aspect-square w-full overflow-hidden bg-muted">
              <img
                v-if="row.cover"
                :src="row.cover"
                :alt="row.title"
                loading="lazy"
                class="h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.02]"
              />
              <div v-else class="flex h-full w-full items-center justify-center text-muted">
                <UIcon name="i-lucide-music" class="size-8" />
              </div>
            </RouterLink>

            <div class="flex flex-1 flex-col gap-1 p-2.5">
              <RouterLink :to="row.href" class="truncate text-sm font-medium hover:underline">
                {{ row.title }}
              </RouterLink>
              <button
                type="button"
                class="truncate text-left text-xs text-muted hover:text-highlighted hover:underline"
                :title="`Filter library by ${row.artist}`"
                @click="filterByArtist(row.artist)"
              >
                {{ row.artist }}
              </button>
              <p class="truncate text-[11px] text-muted">
                <span v-if="row.album">Album: {{ row.album }}</span>
                <span v-else class="italic">No album</span>
              </p>
              <p v-if="row.quality" class="truncate text-[11px] text-muted">{{ row.quality }}</p>
              <div class="mt-auto flex flex-wrap items-center gap-1 pt-1">
                <UButton
                  v-if="row.color === 'red' && row.kind !== 'song'"
                  size="xs"
                  color="primary"
                  variant="soft"
                  :loading="downloadingKey === itemKey(row)"
                  @click="downloadRow(row)"
                >
                  Download
                </UButton>
                <UButton
                  v-if="!row.album"
                  size="xs"
                  color="neutral"
                  variant="ghost"
                  :loading="checkingAlbumKey === itemKey(row)"
                  @click="checkRowAlbum(row)"
                >
                  Check album
                </UButton>
                <UButton
                  size="xs"
                  color="neutral"
                  variant="ghost"
                  @click="openArtistCheck(row.artist)"
                >
                  Artist
                </UButton>
              </div>
            </div>

            <UTooltip :text="row.label">
              <div class="h-1.5 w-full" :class="statusStripClass(row.color)" />
            </UTooltip>
          </div>
        </div>

        <div
          v-else-if="data && data.rows.length > 0"
          class="overflow-x-auto rounded-lg border border-default"
        >
          <table class="w-full text-left text-sm">
            <thead class="border-b border-default bg-elevated/60 text-xs text-muted">
              <tr>
                <th class="w-9 px-3 py-2.5">
                  <UCheckbox
                    :model-value="allVisibleSelected"
                    aria-label="Select all visible songs"
                    @update:model-value="toggleAllVisible"
                  />
                </th>
                <th class="px-3 py-2.5 font-medium">Song</th>
                <th class="hidden lg:table-cell px-3 py-2.5 font-medium">Album</th>
                <th class="hidden md:table-cell px-3 py-2.5 font-medium">Quality</th>
                <th class="hidden sm:table-cell px-3 py-2.5 font-medium">Size</th>
                <th class="hidden sm:table-cell px-3 py-2.5 font-medium">Source</th>
                <th class="px-3 py-2.5 font-medium">Status</th>
                <th class="w-40 px-3 py-2.5 text-right font-medium" />
              </tr>
            </thead>
            <tbody class="divide-y divide-default">
              <tr
                v-for="row in data.rows"
                :key="itemKey(row)"
                class="hover:bg-elevated/40"
              >
                <td class="px-3 py-2">
                  <UCheckbox
                    :model-value="isSelected(row)"
                    :aria-label="`Select ${row.title}`"
                    @update:model-value="toggleSelected(row, $event)"
                  />
                </td>
                <td class="px-3 py-2 max-w-0 w-full">
                  <div class="flex items-center gap-3 min-w-0">
                    <RouterLink :to="row.href" class="size-10 shrink-0 overflow-hidden rounded bg-muted">
                      <img
                        v-if="row.cover"
                        :src="row.cover"
                        :alt="row.title"
                        loading="lazy"
                        class="h-full w-full object-cover"
                      />
                      <div v-else class="flex h-full w-full items-center justify-center text-muted">
                        <UIcon name="i-lucide-music" class="size-4" />
                      </div>
                    </RouterLink>
                    <div class="min-w-0 flex-1">
                      <RouterLink :to="row.href" class="block truncate font-medium hover:underline">
                        {{ row.title }}
                      </RouterLink>
                      <button
                        type="button"
                        class="block max-w-full truncate text-left text-xs text-muted hover:text-highlighted hover:underline"
                        :title="`Filter library by ${row.artist}`"
                        @click="filterByArtist(row.artist)"
                      >
                        {{ row.artist }}
                      </button>
                    </div>
                  </div>
                </td>
                <td class="hidden lg:table-cell px-3 py-2 text-xs text-muted max-w-48">
                  <span v-if="row.album" class="block truncate" :title="row.album">{{ row.album }}</span>
                  <div v-else class="flex items-center gap-1.5">
                    <UBadge color="neutral" variant="subtle" size="xs">No album</UBadge>
                    <UButton
                      size="xs"
                      color="neutral"
                      variant="ghost"
                      :loading="checkingAlbumKey === itemKey(row)"
                      @click="checkRowAlbum(row)"
                    >
                      Check
                    </UButton>
                  </div>
                </td>
                <td class="hidden md:table-cell px-3 py-2 text-xs text-muted whitespace-nowrap">
                  {{ row.quality || '—' }}
                </td>
                <td class="hidden sm:table-cell px-3 py-2 text-xs text-muted whitespace-nowrap">
                  {{ row.size_human || '—' }}
                </td>
                <td class="hidden sm:table-cell px-3 py-2 whitespace-nowrap">
                  <UTooltip :text="sourceMeta(row.kind).label">
                    <UBadge
                      :color="sourceMeta(row.kind).color"
                      variant="subtle"
                      size="xs"
                      :aria-label="sourceMeta(row.kind).label"
                      class="px-1.5 py-1"
                    >
                      <svg v-if="row.kind === 'spotify'" viewBox="0 0 24 24" class="size-3.5 shrink-0 fill-current" aria-hidden="true">
                        <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm4.59 14.42a.62.62 0 0 1-.86.2c-2.35-1.44-5.31-1.76-8.8-.96a.62.62 0 1 1-.28-1.22c3.82-.87 7.1-.5 9.74 1.12.3.18.39.56.2.86Zm1.22-2.72a.78.78 0 0 1-1.07.26c-2.69-1.65-6.79-2.13-9.97-1.16a.78.78 0 1 1-.46-1.5c3.63-1.1 8.15-.57 11.24 1.33.37.22.48.7.26 1.07Zm.11-2.83c-3.22-1.91-8.54-2.09-11.62-1.15a.94.94 0 0 1-.54-1.8c3.53-1.07 9.4-.87 13.11 1.34a.94.94 0 0 1-.95 1.61Z" />
                      </svg>
                      <svg v-else-if="row.kind === 'video'" viewBox="0 0 24 24" class="size-3.5 shrink-0 fill-current" aria-hidden="true">
                        <path d="M21.58 7.19a2.75 2.75 0 0 0-1.94-1.95C17.93 4.78 12 4.78 12 4.78s-5.93 0-7.64.46A2.75 2.75 0 0 0 2.42 7.2 28.6 28.6 0 0 0 1.96 12a28.6 28.6 0 0 0 .46 4.81 2.75 2.75 0 0 0 1.94 1.95c1.71.46 7.64.46 7.64.46s5.93 0 7.64-.46a2.75 2.75 0 0 0 1.94-1.95A28.6 28.6 0 0 0 22.04 12a28.6 28.6 0 0 0-.46-4.81ZM9.98 15.02V8.98L15.22 12l-5.24 3.02Z" />
                      </svg>
                      <UIcon v-else name="i-lucide-hard-drive" class="size-3.5 shrink-0" />
                    </UBadge>
                  </UTooltip>
                </td>
                <td class="px-3 py-2 whitespace-nowrap">
                  <UBadge
                    :color="row.color === 'green' ? 'success' : row.color === 'yellow' ? 'warning' : 'error'"
                    variant="subtle"
                    size="xs"
                  >
                    {{ row.label }}
                  </UBadge>
                </td>
                <td class="px-3 py-2 text-right whitespace-nowrap">
                  <div class="inline-flex items-center gap-1">
                    <UButton
                      size="xs"
                      color="neutral"
                      variant="ghost"
                      @click="openArtistCheck(row.artist)"
                    >
                      Artist
                    </UButton>
                    <UButton
                      v-if="row.color === 'red' && row.kind !== 'song'"
                      size="xs"
                      color="primary"
                      variant="soft"
                      :loading="downloadingKey === itemKey(row)"
                      @click="downloadRow(row)"
                    >
                      Download
                    </UButton>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div
          v-if="selected.length > 0"
          class="sticky bottom-2 z-20 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-default bg-default/95 p-3 shadow-lg backdrop-blur"
        >
          <span class="text-sm font-medium">{{ selected.length }} selected</span>
          <div class="flex flex-wrap items-center gap-2">
            <UButton size="sm" variant="ghost" color="neutral" @click="selected = []">
              Clear
            </UButton>
            <UButton
              size="sm"
              color="primary"
              :loading="busy === 'bulk-redownload'"
              @click="runBulk('redownload')"
            >
              Re-download selected
            </UButton>
            <UButton
              size="sm"
              color="error"
              variant="outline"
              :loading="busy === 'bulk-delete'"
              @click="runBulk('delete')"
            >
              Delete selected
            </UButton>
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-4 pt-2 text-xs text-muted">
          <span class="inline-flex items-center gap-1.5">
            <span class="status-dot inline-block size-2 rounded-full bg-success" />
            On disk
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="status-dot inline-block size-2 rounded-full bg-error" />
            Missing
          </span>
          <span class="inline-flex items-center gap-1.5">
            <span class="status-dot inline-block size-2 rounded-full bg-warning" />
            Queued or running
          </span>
        </div>
        <UModal
          v-model:open="artistCheckOpen"
          :title="artistCheckData ? `${artistCheckData.artist} on YouTube Music` : `Checking ${artistCheckQuery}`"
          :ui="{ content: 'sm:max-w-2xl' }"
        >
          <template #body>
            <div class="flex flex-col gap-3">
              <UAlert v-if="artistCheckError" color="error" variant="subtle" :description="artistCheckError" />
              <div v-else-if="artistCheckLoading" class="py-8 text-center text-sm text-muted">
                Loading artist releases from YouTube Music...
              </div>
              <div v-else-if="artistCheckData && !artistCheckData.found" class="py-6 text-center text-sm text-muted">
                Artist "{{ artistCheckData.artist }}" was not found on YouTube Music.
              </div>
              <template v-else-if="artistCheckData">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <p class="text-xs text-muted">
                    {{ artistCheckData.owned_count }} owned · {{ artistCheckData.missing_count }} missing ({{ artistCheckData.total }} total)
                  </p>
                  <UFieldGroup size="xs">
                    <UButton
                      :color="artistCheckFilter === 'all' ? 'primary' : 'neutral'"
                      :variant="artistCheckFilter === 'all' ? 'solid' : 'outline'"
                      @click="artistCheckFilter = 'all'"
                    >
                      All ({{ artistCheckData.total }})
                    </UButton>
                    <UButton
                      :color="artistCheckFilter === 'missing' ? 'primary' : 'neutral'"
                      :variant="artistCheckFilter === 'missing' ? 'solid' : 'outline'"
                      @click="artistCheckFilter = 'missing'"
                    >
                      Missing ({{ artistCheckData.missing_count }})
                    </UButton>
                    <UButton
                      :color="artistCheckFilter === 'owned' ? 'primary' : 'neutral'"
                      :variant="artistCheckFilter === 'owned' ? 'solid' : 'outline'"
                      @click="artistCheckFilter = 'owned'"
                    >
                      Owned ({{ artistCheckData.owned_count }})
                    </UButton>
                  </UFieldGroup>
                </div>

                <div class="max-h-96 divide-y divide-default overflow-y-auto rounded-lg border border-default">
                  <div
                    v-if="filteredArtistTracks.length === 0"
                    class="py-6 text-center text-sm text-muted"
                  >
                    No tracks in this filter.
                  </div>
                  <div
                    v-for="track in filteredArtistTracks"
                    :key="track.video_id"
                    class="flex items-center justify-between gap-3 px-3 py-2 text-sm"
                  >
                    <div class="min-w-0 flex-1">
                      <p class="truncate font-medium">{{ track.title }}</p>
                      <p class="truncate text-xs text-muted">
                        {{ track.artists }} ·
                        <span v-if="track.in_album">Album: {{ track.album }}</span>
                        <span v-else class="italic">No album / single</span>
                      </p>
                    </div>
                    <UBadge v-if="track.owned" color="success" variant="subtle" size="xs">
                      Owned
                    </UBadge>
                    <UBadge v-else-if="track.active" color="warning" variant="subtle" size="xs">
                      Queued
                    </UBadge>
                    <UButton
                      v-else
                      size="xs"
                      color="primary"
                      variant="soft"
                      :loading="queueingVideoId === track.video_id"
                      @click="queueArtistTrack(track)"
                    >
                      Queue
                    </UButton>
                  </div>
                </div>
              </template>
            </div>
          </template>
        </UModal>
      </div>
    </template>
  </UDashboardPanel>
</template>
