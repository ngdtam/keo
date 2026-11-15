<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiGet } from '../api'

interface AttentionItem {
  href: string
  artist: string
  title: string
}

interface DashboardData {
  review_count: number
  library_count: number
  library_size: string
  missing_count: number
  active_count: number
  failed_count: number
  attention: AttentionItem[]
  auth_error: string
  gaps: string[]
  warnings: string[]
}

const router = useRouter()
const data = ref<DashboardData | null>(null)
const error = ref('')
const searchQuery = ref('')

onMounted(async () => {
  try {
    data.value = await apiGet<DashboardData>('/api/dashboard')
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load dashboard'
  }
})

function onSearch() {
  const q = searchQuery.value.trim()
  if (q) {
    router.push({ path: '/search', query: { q } })
  }
}
</script>

<template>
  <UDashboardPanel id="home">
    <template #header>
      <UDashboardNavbar title="Home">
        <template #right>
          <form @submit.prevent="onSearch">
            <UInput
              v-model="searchQuery"
              placeholder="Search..."
              icon="i-lucide-search"
              size="sm"
            />
          </form>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <UAlert
          v-if="data && (data.auth_error || data.gaps.length || data.warnings.length)"
          color="warning"
          variant="subtle"
          title="Attention needed"
        >
          <template #description>
            <ul class="list-disc list-inside space-y-1 text-sm">
              <li v-if="data.auth_error">{{ data.auth_error }}</li>
              <li v-for="gap in data.gaps" :key="gap">{{ gap }}</li>
              <li v-for="warn in data.warnings" :key="warn">{{ warn }}</li>
            </ul>
          </template>
        </UAlert>

        <div v-if="data" class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <UCard>
            <template #header>
              <span class="font-medium">Review</span>
            </template>
            <p class="text-2xl font-semibold">{{ data.review_count }} waiting</p>
            <template #footer>
              <RouterLink to="/review" class="text-sm text-primary hover:underline">
                Open review
              </RouterLink>
            </template>
          </UCard>

          <UCard>
            <template #header>
              <span class="font-medium">Library</span>
            </template>
            <p class="text-2xl font-semibold">{{ data.library_count }} songs</p>
            <p class="text-sm text-muted mt-1">
              {{ data.library_size }} · {{ data.missing_count }} missing
            </p>
            <template #footer>
              <RouterLink to="/library" class="text-sm text-primary hover:underline">
                Open library
              </RouterLink>
            </template>
          </UCard>

          <UCard>
            <template #header>
              <span class="font-medium">Jobs</span>
            </template>
            <div class="flex flex-wrap gap-2">
              <UBadge color="warning" variant="subtle">
                {{ data.active_count }} queued or running
              </UBadge>
              <UBadge color="error" variant="subtle">
                {{ data.failed_count }} failed
              </UBadge>
            </div>
            <ul v-if="data.attention.length" class="mt-3 space-y-1 text-sm">
              <li v-for="item in data.attention" :key="item.href">
                <RouterLink :to="item.href" class="hover:underline">
                  {{ item.artist }} — {{ item.title }}
                </RouterLink>
              </li>
            </ul>
            <template #footer>
              <RouterLink to="/library" class="text-sm text-primary hover:underline">
                Open library
              </RouterLink>
            </template>
          </UCard>
        </div>
      </div>
    </template>
  </UDashboardPanel>
</template>
