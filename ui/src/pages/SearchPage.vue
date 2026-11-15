<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiGet } from '../api'
import SongList, { type SongItem } from '../components/SongList.vue'

const route = useRoute()
const router = useRouter()

const query = ref('')
const suggestions = ref<string[]>([])
const songs = ref<SongItem[]>([])
const searching = ref(false)
const hasSearched = ref(false)
const error = ref('')

let suggestTimer: ReturnType<typeof setTimeout> | undefined

onBeforeUnmount(() => {
  if (suggestTimer) clearTimeout(suggestTimer)
})

watch(query, (val) => {
  if (suggestTimer) clearTimeout(suggestTimer)
  const trimmed = val.trim()
  if (!trimmed) {
    suggestions.value = []
    return
  }
  suggestTimer = setTimeout(async () => {
    try {
      suggestions.value = await apiGet<string[]>(
        `/api/suggestions?q=${encodeURIComponent(trimmed)}`,
      )
    } catch {
      suggestions.value = []
    }
  }, 250)
})

async function runSearch(q: string) {
  searching.value = true
  error.value = ''
  hasSearched.value = true
  try {
    songs.value = await apiGet<SongItem[]>(
      `/api/search?q=${encodeURIComponent(q)}`,
    )
  } catch (err) {
    songs.value = []
    error.value = err instanceof Error ? err.message : 'Search failed'
  } finally {
    searching.value = false
  }
}

watch(
  () => route.query.q,
  (val) => {
    const q = typeof val === 'string' ? val.trim() : ''
    query.value = q
    if (q) {
      runSearch(q)
    } else {
      songs.value = []
      error.value = ''
      hasSearched.value = false
    }
  },
  { immediate: true },
)

function submitSearch() {
  const q = query.value.trim()
  if (!q) {
    router.push({ path: '/search' })
    return
  }
  const current = typeof route.query.q === 'string' ? route.query.q.trim() : ''
  if (q === current) {
    runSearch(q)
  } else {
    router.push({ path: '/search', query: { q } })
  }
}
</script>

<template>
  <UDashboardPanel id="search">
    <template #header>
      <UDashboardNavbar title="Search" />
      <UDashboardToolbar>
        <form class="flex items-center gap-2 w-full" @submit.prevent="submitSearch">
          <UInput
            v-model="query"
            placeholder="Search songs..."
            icon="i-lucide-search"
            list="search-suggestions"
            class="flex-1"
          />
          <datalist id="search-suggestions">
            <option v-for="item in suggestions" :key="item" :value="item" />
          </datalist>
          <UButton type="submit" color="primary" :loading="searching">
            Search
          </UButton>
        </form>
      </UDashboardToolbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />
        <p v-if="searching" class="text-sm text-muted">Searching...</p>
        <SongList v-else-if="hasSearched" :songs="songs" empty-text="No songs found." />
      </div>
    </template>
  </UDashboardPanel>
</template>
