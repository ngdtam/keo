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
  stats: string
  total_count: number
  missing_count: number
  upgradable_count: number
  unmatched_count: number
}

const COVER_SIZE_KEY = 'keo_cover_size'
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
const coverSize = ref(readStoredCoverSize())

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
])

watch(coverSize, (val) => {
  localStorage.setItem(COVER_SIZE_KEY, String(val))
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

function statusStripClass(color: LibraryRow['color']): string {
  if (color === 'green') return 'bg-success'
  if (color === 'yellow') return 'bg-warning'
  return 'bg-error'
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
  await router.replace({ query })
  await loadLibrary()
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
              placeholder="Search artist or title..."
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
            <div class="flex items-center gap-2 w-28" title="Cover size">
              <UIcon name="i-lucide-grid-2x2" class="size-4 text-muted shrink-0" />
              <USlider v-model="coverSize" :min="96" :max="320" :step="8" size="sm" />
            </div>
          </div>
        </template>
      </UDashboardToolbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <div
          v-if="data && data.rows.length === 0 && !loading"
          class="py-12 text-center text-sm text-muted"
        >
          No songs match the current filter.
        </div>

        <div
          v-if="data && data.rows.length > 0"
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
              <p class="truncate text-xs text-muted">{{ row.artist }}</p>
              <p v-if="row.quality" class="truncate text-[11px] text-muted">{{ row.quality }}</p>
              <UButton
                v-if="row.color === 'red' && row.kind !== 'song'"
                size="xs"
                color="primary"
                variant="soft"
                class="mt-auto self-start"
                :loading="downloadingKey === itemKey(row)"
                @click="downloadRow(row)"
              >
                Download
              </UButton>
            </div>

            <UTooltip :text="row.label">
              <div class="h-1.5 w-full" :class="statusStripClass(row.color)" />
            </UTooltip>
          </div>
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
      </div>
    </template>
  </UDashboardPanel>
</template>
