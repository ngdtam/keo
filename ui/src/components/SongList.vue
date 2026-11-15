<script lang="ts">
export interface SongItem {
  video_id: string
  title: string
  artists: string[]
  album: string | null
}
</script>

<script setup lang="ts">
import { reactive } from 'vue'
import { apiPostJson } from '../api'

withDefaults(
  defineProps<{
    songs: SongItem[]
    emptyText?: string
  }>(),
  { emptyText: 'No songs.' },
)

const toast = useToast()
const loading = reactive<Record<string, boolean>>({})
const queued = reactive<Record<string, boolean>>({})

async function download(song: SongItem) {
  loading[song.video_id] = true
  try {
    await apiPostJson<{ owned: boolean; job: unknown }>('/api/downloads', {
      video_id: song.video_id,
      title: song.title,
      artists: song.artists,
    })
    queued[song.video_id] = true
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Download failed',
      color: 'error',
    })
  } finally {
    loading[song.video_id] = false
  }
}
</script>

<template>
  <p v-if="songs.length === 0" class="text-sm text-muted">
    {{ emptyText }}
  </p>

  <div v-else class="flex flex-col gap-2">
    <UCard v-for="song in songs" :key="song.video_id">
      <div class="flex items-center justify-between gap-4">
        <div class="min-w-0 flex-1">
          <p class="font-medium truncate">{{ song.title }}</p>
          <p class="text-sm text-muted truncate">
            {{ song.artists.join(', ') }}
            <template v-if="song.album"> · {{ song.album }}</template>
          </p>
        </div>

        <div class="flex items-center gap-2 shrink-0">
          <UButton
            variant="ghost"
            color="neutral"
            size="sm"
            :to="`/songs/${encodeURIComponent(song.video_id)}/radio`"
          >
            Radio
          </UButton>
          <UButton
            color="primary"
            size="sm"
            :disabled="queued[song.video_id] || loading[song.video_id]"
            :loading="loading[song.video_id]"
            @click="download(song)"
          >
            {{ queued[song.video_id] ? 'Queued' : 'Download' }}
          </UButton>
        </div>
      </div>
      <UProgress v-if="queued[song.video_id]" size="xs" color="primary" class="mt-2" />
    </UCard>
  </div>
</template>
