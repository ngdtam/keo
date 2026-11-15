<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { apiGet } from '../api'
import SongList, { type SongItem } from '../components/SongList.vue'

interface PlaylistRef {
  playlist_id: string
  title: string
}

interface MoodRef {
  params: string
  title: string
}

type Mode = 'playlists' | 'moods' | 'songs'

const route = useRoute()

const mode = ref<Mode>('playlists')
const title = ref('Browse')
const loading = ref(false)
const error = ref('')
const playlists = ref<PlaylistRef[]>([])
const moodSections = ref<Record<string, MoodRef[]>>({})
const songs = ref<SongItem[]>([])

async function load() {
  loading.value = true
  error.value = ''
  playlists.value = []
  moodSections.value = {}
  songs.value = []

  try {
    if (route.path === '/charts') {
      mode.value = 'playlists'
      title.value = 'Vietnam charts'
      playlists.value = await apiGet<PlaylistRef[]>('/api/charts')
    } else if (route.path === '/moods') {
      mode.value = 'moods'
      title.value = 'Moods & genres'
      moodSections.value = await apiGet<Record<string, MoodRef[]>>('/api/moods')
    } else if (route.path.startsWith('/moods/')) {
      mode.value = 'playlists'
      title.value = 'Mood playlists'
      const params = String(route.params.params ?? '')
      playlists.value = await apiGet<PlaylistRef[]>(
        `/api/moods/${encodeURIComponent(params)}`,
      )
    } else if (route.path.startsWith('/playlists/')) {
      mode.value = 'songs'
      title.value = 'Playlist'
      const id = String(route.params.id ?? '')
      const res = await apiGet<{ title: string; songs: SongItem[] }>(
        `/api/playlists/${encodeURIComponent(id)}`,
      )
      title.value = res.title || 'Playlist'
      songs.value = res.songs
    } else if (route.path.endsWith('/radio')) {
      mode.value = 'songs'
      title.value = 'Radio'
      const videoId = String(route.params.videoId ?? '')
      songs.value = await apiGet<SongItem[]>(
        `/api/songs/${encodeURIComponent(videoId)}/radio`,
      )
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load page'
  } finally {
    loading.value = false
  }
}

watch(() => route.fullPath, load, { immediate: true })
</script>

<template>
  <UDashboardPanel id="browse">
    <template #header>
      <UDashboardNavbar :title="title" />
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />
        <p v-if="loading" class="text-sm text-muted">Loading...</p>

        <template v-else-if="!error">
          <div
            v-if="mode === 'playlists'"
            class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"
          >
            <RouterLink
              v-for="p in playlists"
              :key="p.playlist_id"
              :to="`/playlists/${encodeURIComponent(p.playlist_id)}`"
              class="block"
            >
              <UCard class="h-full hover:ring-1 hover:ring-primary transition">
                <p class="font-medium">{{ p.title || p.playlist_id }}</p>
              </UCard>
            </RouterLink>
          </div>

          <div v-else-if="mode === 'moods'" class="flex flex-col gap-6">
            <section v-for="(items, section) in moodSections" :key="section">
              <h2 class="text-base font-semibold mb-2">{{ section }}</h2>
              <div class="flex flex-wrap gap-2">
                <UButton
                  v-for="m in items"
                  :key="m.params"
                  variant="outline"
                  color="neutral"
                  size="sm"
                  :to="`/moods/${encodeURIComponent(m.params)}`"
                >
                  {{ m.title }}
                </UButton>
              </div>
            </section>
          </div>

          <SongList v-else :songs="songs" />
        </template>
      </div>
    </template>
  </UDashboardPanel>
</template>
