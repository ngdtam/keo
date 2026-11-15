<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiGet, apiPostForm, apiPostJson } from '../api'

interface ReviewRow {
  video_id: string
  heard_artist: string
  heard_title: string
  candidate_artist: string | null
  candidate_title: string | null
  spotify_url: string | null
  spotify_id: string | null
  reason: string
  created_at: string
}

interface WatchedPlaylist {
  playlist_id: string
  title: string
  auto_queue: boolean
  last_synced_at: string | null
  created_at: string
}

interface FollowedArtist {
  channel_id: string
  name: string
  last_synced_at: string | null
  created_at: string
}

interface HistoryProgress {
  active: boolean
  stage: string
  current: number
  total: number
  percent: number
}

interface ReviewFullResponse {
  review: ReviewRow[]
  watched_playlists: WatchedPlaylist[]
  followed_artists: FollowedArtist[]
  history_progress: HistoryProgress
}

interface SongCandidate {
  video_id: string
  title: string
  artists: string[]
  album: string | null
}

interface CandidatesResponse {
  item: ReviewRow
  query: string
  candidates: SongCandidate[]
  error: string
}

const route = useRoute()
const router = useRouter()
const toast = useToast()

const videoId = computed(() =>
  typeof route.params.videoId === 'string' ? route.params.videoId : '',
)

const loading = ref(false)
const error = ref('')
const busy = ref('')

// /review state
const review = ref<ReviewRow[]>([])
const watchedPlaylists = ref<WatchedPlaylist[]>([])
const followedArtists = ref<FollowedArtist[]>([])
const progress = ref<HistoryProgress>({
  active: false,
  stage: 'idle',
  current: 0,
  total: 0,
  percent: 0,
})
const playlistInput = ref('')
const playlistAutoQueue = ref(false)
const artistInput = ref('')

// /review/:videoId/pick state
const pickData = ref<CandidatesResponse | null>(null)
const pickQuery = ref('')

let pollTimer: ReturnType<typeof setInterval> | undefined

function stopPolling() {
  if (pollTimer !== undefined) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

onBeforeUnmount(stopPolling)

function showError(err: unknown, fallback: string) {
  toast.add({
    title: err instanceof Error ? err.message : fallback,
    color: 'error',
  })
}

function startPolling() {
  if (pollTimer !== undefined) return
  pollTimer = setInterval(async () => {
    try {
      const snap = await apiGet<HistoryProgress>('/api/history/status')
      progress.value = snap
      if (!snap.active) {
        stopPolling()
        await loadReview()
      }
    } catch {
      stopPolling()
    }
  }, 1000)
}

async function loadReview() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiGet<ReviewFullResponse>('/api/review/full')
    review.value = res.review
    watchedPlaylists.value = res.watched_playlists
    followedArtists.value = res.followed_artists
    progress.value = res.history_progress
    if (res.history_progress.active) {
      startPolling()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load review'
  } finally {
    loading.value = false
  }
}

async function loadCandidates(id: string, q?: string) {
  loading.value = true
  error.value = ''
  try {
    const qs = q && q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''
    const res = await apiGet<CandidatesResponse>(
      `/api/review/${encodeURIComponent(id)}/candidates${qs}`,
    )
    pickData.value = res
    pickQuery.value = res.query
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load candidates'
  } finally {
    loading.value = false
  }
}

watch(
  videoId,
  (id) => {
    stopPolling()
    if (id) {
      loadCandidates(id)
    } else {
      pickData.value = null
      loadReview()
    }
  },
  { immediate: true },
)

async function checkHistoryNow() {
  busy.value = 'pull'
  try {
    await apiPostJson('/api/history/pull')
    progress.value = { ...progress.value, active: true, stage: 'fetching' }
    startPolling()
  } catch (err) {
    showError(err, 'Failed to start history check')
  } finally {
    busy.value = ''
  }
}

async function downloadReviewItem(item: ReviewRow, youtubeOnly: boolean) {
  const key = `${youtubeOnly ? 'yt' : 'dl'}:${item.video_id}`
  busy.value = key
  try {
    const suffix = youtubeOnly ? 'download-youtube' : 'download'
    await apiPostJson(`/api/review/${encodeURIComponent(item.video_id)}/${suffix}`)
    toast.add({
      title: `Queued ${item.heard_title || item.video_id}`,
      color: 'success',
    })
    if (videoId.value) {
      await router.push('/review')
    } else {
      await loadReview()
    }
  } catch (err) {
    showError(err, 'Download failed')
  } finally {
    busy.value = ''
  }
}

async function dismissItem(item: ReviewRow) {
  busy.value = `dismiss:${item.video_id}`
  try {
    await apiPostJson(`/api/review/${encodeURIComponent(item.video_id)}/dismiss`)
    toast.add({ title: 'Dismissed', color: 'neutral' })
    await loadReview()
  } catch (err) {
    showError(err, 'Dismiss failed')
  } finally {
    busy.value = ''
  }
}

async function watchPlaylist() {
  const pid = playlistInput.value.trim()
  if (!pid) return
  busy.value = 'watch-playlist'
  try {
    await apiPostForm('/review/playlists/watch', {
      playlist_id: pid,
      auto_queue: playlistAutoQueue.value ? 'true' : 'false',
    })
    playlistInput.value = ''
    playlistAutoQueue.value = false
    toast.add({ title: 'Playlist added', color: 'success' })
    await loadReview()
  } catch (err) {
    showError(err, 'Failed to watch playlist')
  } finally {
    busy.value = ''
  }
}

async function syncPlaylists() {
  busy.value = 'sync-playlists'
  try {
    await apiPostForm('/review/playlists/sync')
    toast.add({ title: 'Playlists synced', color: 'success' })
    await loadReview()
  } catch (err) {
    showError(err, 'Failed to sync playlists')
  } finally {
    busy.value = ''
  }
}

async function unwatchPlaylist(playlistId: string) {
  busy.value = `unwatch:${playlistId}`
  try {
    await apiPostForm(`/review/playlists/${encodeURIComponent(playlistId)}/unwatch`)
    await loadReview()
  } catch (err) {
    showError(err, 'Failed to unwatch playlist')
  } finally {
    busy.value = ''
  }
}

async function followArtist() {
  const artist = artistInput.value.trim()
  if (!artist) return
  busy.value = 'follow-artist'
  try {
    await apiPostForm('/review/artists/follow', { artist })
    artistInput.value = ''
    toast.add({ title: 'Artist followed', color: 'success' })
    await loadReview()
  } catch (err) {
    showError(err, 'Failed to follow artist')
  } finally {
    busy.value = ''
  }
}

async function syncArtists() {
  busy.value = 'sync-artists'
  try {
    await apiPostForm('/review/artists/sync')
    toast.add({ title: 'Artists synced', color: 'success' })
    await loadReview()
  } catch (err) {
    showError(err, 'Failed to sync artists')
  } finally {
    busy.value = ''
  }
}

async function unfollowArtist(channelId: string) {
  busy.value = `unfollow:${channelId}`
  try {
    await apiPostForm(`/review/artists/${encodeURIComponent(channelId)}/unfollow`)
    await loadReview()
  } catch (err) {
    showError(err, 'Failed to unfollow artist')
  } finally {
    busy.value = ''
  }
}

async function pickCandidate(candidate: SongCandidate) {
  if (!videoId.value) return
  busy.value = `pick:${candidate.video_id}`
  try {
    await apiPostForm(`/review/${encodeURIComponent(videoId.value)}/pick`, {
      chosen_video_id: candidate.video_id,
      artist: candidate.artists[0] ?? '',
      title: candidate.title,
    })
    toast.add({ title: `Queued ${candidate.title}`, color: 'success' })
    await router.push('/review')
  } catch (err) {
    showError(err, 'Failed to pick match')
  } finally {
    busy.value = ''
  }
}

const progressLabel = computed(() => {
  if (!progress.value.active) return ''
  if (progress.value.total > 0) {
    return `${progress.value.stage} ${progress.value.current}/${progress.value.total}`
  }
  return progress.value.stage === 'fetching' ? 'Fetching history…' : 'Checking…'
})
</script>

<template>
  <UDashboardPanel id="review">
    <template #header>
      <UDashboardNavbar :title="videoId ? 'Pick match' : 'Review'">
        <template #right>
          <div v-if="videoId" class="flex items-center gap-2">
            <UButton
              to="/review"
              variant="outline"
              color="neutral"
              size="sm"
              icon="i-lucide-arrow-left"
            >
              Back to Review
            </UButton>
            <UButton
              v-if="pickData"
              variant="outline"
              color="neutral"
              size="sm"
              :loading="busy === `yt:${videoId}`"
              @click="downloadReviewItem(pickData.item, true)"
            >
              Download from YouTube directly
            </UButton>
          </div>
          <div v-else class="flex items-center gap-2">
            <UBadge v-if="progress.active" color="primary" variant="subtle">
              {{ progressLabel }}
            </UBadge>
            <UButton
              color="primary"
              size="sm"
              icon="i-lucide-refresh-cw"
              :loading="progress.active || busy === 'pull'"
              @click="checkHistoryNow"
            >
              Check history now
            </UButton>
          </div>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <UCard v-if="!videoId && progress.active">
          <div class="flex flex-col gap-2">
            <span class="text-xs font-medium text-muted">{{ progressLabel }}</span>
            <UProgress
              v-if="progress.total > 0"
              v-model="progress.current"
              :max="progress.total"
              status
              size="sm"
            />
            <UProgress v-else size="sm" />
          </div>
        </UCard>

        <!-- Pick match mode -->
        <template v-if="videoId">
          <p v-if="loading && !pickData" class="text-sm text-muted">Loading candidates…</p>
          <template v-else-if="pickData">
            <UCard>
              <div class="flex flex-col gap-3">
                <div>
                  <p class="font-medium">
                    {{ pickData.item.heard_artist }} — {{ pickData.item.heard_title }}
                  </p>
                  <p class="text-xs text-muted">{{ pickData.item.reason }}</p>
                </div>
                <form
                  class="flex items-center gap-2"
                  @submit.prevent="loadCandidates(videoId, pickQuery)"
                >
                  <UInput
                    v-model="pickQuery"
                    placeholder="Search catalog..."
                    class="flex-1"
                  />
                  <UButton type="submit" color="neutral" variant="outline" :loading="loading">
                    Search
                  </UButton>
                </form>
              </div>
            </UCard>

            <UAlert
              v-if="pickData.error"
              color="error"
              variant="subtle"
              :description="pickData.error"
            />

            <UCard>
              <template #header>
                <span class="font-medium text-sm">Candidates</span>
              </template>
              <div
                v-if="pickData.candidates.length === 0"
                class="py-6 text-center text-sm text-muted"
              >
                No matching candidates found.
              </div>
              <div v-else class="divide-y divide-default">
                <div
                  v-for="c in pickData.candidates"
                  :key="c.video_id"
                  class="py-3 flex items-center justify-between gap-4"
                >
                  <div class="min-w-0">
                    <p class="font-medium text-sm truncate">{{ c.title }}</p>
                    <p class="text-xs text-muted truncate">
                      {{ c.artists.join(', ') }}
                      <template v-if="c.album"> · {{ c.album }}</template>
                    </p>
                  </div>
                  <UButton
                    size="xs"
                    color="primary"
                    :loading="busy === `pick:${c.video_id}`"
                    @click="pickCandidate(c)"
                  >
                    Use this match
                  </UButton>
                </div>
              </div>
            </UCard>
          </template>
        </template>

        <!-- Review list mode -->
        <template v-else>
          <UCard>
            <template #header>
              <div class="flex items-center justify-between">
                <span class="font-medium text-sm">Needs review ({{ review.length }})</span>
              </div>
            </template>

            <p v-if="loading && review.length === 0" class="py-6 text-center text-sm text-muted">
              Loading…
            </p>
            <p v-else-if="review.length === 0" class="py-6 text-center text-sm text-muted">
              Nothing to review right now.
            </p>
            <div v-else class="divide-y divide-default">
              <div
                v-for="item in review"
                :key="item.video_id"
                class="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3"
              >
                <div class="min-w-0">
                  <p class="font-medium text-sm truncate">
                    {{ item.heard_artist }} — {{ item.heard_title }}
                  </p>
                  <p class="text-xs text-muted">
                    {{ item.reason }}
                    <template v-if="item.candidate_artist || item.candidate_title">
                      · Match: {{ item.candidate_artist }} — {{ item.candidate_title }}
                    </template>
                  </p>
                </div>

                <div class="flex flex-wrap items-center gap-2 shrink-0">
                  <UButton
                    size="xs"
                    color="primary"
                    :loading="busy === `dl:${item.video_id}`"
                    @click="downloadReviewItem(item, false)"
                  >
                    Download
                  </UButton>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === `yt:${item.video_id}`"
                    @click="downloadReviewItem(item, true)"
                  >
                    YouTube
                  </UButton>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :to="`/review/${encodeURIComponent(item.video_id)}/pick`"
                  >
                    Pick match
                  </UButton>
                  <UButton
                    size="xs"
                    color="error"
                    variant="ghost"
                    :loading="busy === `dismiss:${item.video_id}`"
                    @click="dismissItem(item)"
                  >
                    Dismiss
                  </UButton>
                </div>
              </div>
            </div>
          </UCard>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
            <UCard>
              <template #header>
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium text-sm">Watched playlists</span>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === 'sync-playlists'"
                    @click="syncPlaylists"
                  >
                    Sync now
                  </UButton>
                </div>
              </template>

              <div class="flex flex-col gap-4">
                <form class="flex flex-col gap-2" @submit.prevent="watchPlaylist">
                  <div class="flex items-center gap-2">
                    <UInput
                      v-model="playlistInput"
                      placeholder="Playlist ID or URL"
                      class="flex-1"
                    />
                    <UButton
                      type="submit"
                      size="sm"
                      color="primary"
                      :loading="busy === 'watch-playlist'"
                    >
                      Watch
                    </UButton>
                  </div>
                  <UCheckbox v-model="playlistAutoQueue" label="Auto-queue new tracks" />
                </form>

                <p v-if="watchedPlaylists.length === 0" class="text-xs text-muted">
                  No watched playlists yet.
                </p>
                <div v-else class="divide-y divide-default">
                  <div
                    v-for="p in watchedPlaylists"
                    :key="p.playlist_id"
                    class="py-2 flex items-center justify-between gap-2"
                  >
                    <div class="min-w-0">
                      <p class="text-sm font-medium truncate">{{ p.title }}</p>
                      <p class="text-xs text-muted truncate">
                        {{ p.playlist_id }}
                        <template v-if="p.auto_queue"> · auto-queue</template>
                      </p>
                    </div>
                    <UButton
                      size="xs"
                      color="error"
                      variant="ghost"
                      :loading="busy === `unwatch:${p.playlist_id}`"
                      @click="unwatchPlaylist(p.playlist_id)"
                    >
                      Unwatch
                    </UButton>
                  </div>
                </div>
              </div>
            </UCard>

            <UCard>
              <template #header>
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium text-sm">Followed artists</span>
                  <UButton
                    size="xs"
                    color="neutral"
                    variant="outline"
                    :loading="busy === 'sync-artists'"
                    @click="syncArtists"
                  >
                    Sync now
                  </UButton>
                </div>
              </template>

              <div class="flex flex-col gap-4">
                <form class="flex items-center gap-2" @submit.prevent="followArtist">
                  <UInput
                    v-model="artistInput"
                    placeholder="Artist name or channel URL"
                    class="flex-1"
                  />
                  <UButton
                    type="submit"
                    size="sm"
                    color="primary"
                    :loading="busy === 'follow-artist'"
                  >
                    Follow
                  </UButton>
                </form>

                <p v-if="followedArtists.length === 0" class="text-xs text-muted">
                  No followed artists yet.
                </p>
                <div v-else class="divide-y divide-default">
                  <div
                    v-for="a in followedArtists"
                    :key="a.channel_id"
                    class="py-2 flex items-center justify-between gap-2"
                  >
                    <div class="min-w-0">
                      <p class="text-sm font-medium truncate">{{ a.name }}</p>
                      <p class="text-xs text-muted truncate">{{ a.channel_id }}</p>
                    </div>
                    <UButton
                      size="xs"
                      color="error"
                      variant="ghost"
                      :loading="busy === `unfollow:${a.channel_id}`"
                      @click="unfollowArtist(a.channel_id)"
                    >
                      Unfollow
                    </UButton>
                  </div>
                </div>
              </div>
            </UCard>
          </div>
        </template>
      </div>
    </template>
  </UDashboardPanel>
</template>
