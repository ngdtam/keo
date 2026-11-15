<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { NavigationMenuItem } from '@nuxt/ui'
import { apiGet, apiPostJson } from './api'

interface SidebarDashboard {
  nav_review_count?: number
  nav_active_jobs_count?: number
  nav_settings_warn?: boolean
}

const route = useRoute()
const router = useRouter()
const reviewCount = ref(0)
const activeJobsCount = ref(0)
const settingsWarn = ref(false)

async function refreshSidebar() {
  await router.isReady()
  if (route.path === '/login') return
  try {
    const data = await apiGet<SidebarDashboard>('/api/dashboard')
    reviewCount.value = data.nav_review_count ?? 0
    activeJobsCount.value = data.nav_active_jobs_count ?? 0
    settingsWarn.value = Boolean(data.nav_settings_warn)
  } catch {
    // 401 redirects to /login; other errors leave prior badge state
  }
}

function goSearch() {
  if (route.path !== '/search') {
    router.push('/search')
  } else {
    document.querySelector<HTMLInputElement>('#search input')?.focus()
  }
}

defineShortcuts({
  meta_k: goSearch,
})

watch(() => route.path, refreshSidebar, { immediate: true })

const navItems = computed<NavigationMenuItem[]>(() => [
  { label: 'Home', icon: 'i-lucide-house', to: '/' },
  { label: 'Library', icon: 'i-lucide-library', to: '/library' },
  { label: 'Search', icon: 'i-lucide-search', to: '/search' },
  { label: 'Charts', icon: 'i-lucide-trending-up', to: '/charts' },
  { label: 'Moods', icon: 'i-lucide-sparkles', to: '/moods' },
  {
    label: 'Review',
    icon: 'i-lucide-inbox',
    to: '/review',
    badge: reviewCount.value > 0 ? String(reviewCount.value) : undefined,
  },
  {
    label: 'Activity',
    icon: 'i-lucide-activity',
    to: '/activity',
    badge:
      activeJobsCount.value > 0
        ? { label: String(activeJobsCount.value), color: 'primary' }
        : undefined,
  },
  {
    label: 'Settings',
    icon: 'i-lucide-settings',
    to: '/settings',
    badge: settingsWarn.value ? { label: '!', color: 'warning' } : undefined,
  },
])

async function signOut() {
  await apiPostJson('/api/auth/logout')
  await router.push('/login')
}
</script>

<template>
  <UApp>
    <RouterView v-if="route.path === '/login'" />

    <UDashboardGroup v-else>
      <UDashboardSidebar collapsible resizable>
        <template #header="{ collapsed }">
          <RouterLink to="/" class="font-semibold text-base tracking-tight px-1">
            {{ collapsed ? 'K' : 'Kéo' }}
          </RouterLink>
          <UDashboardSidebarCollapse class="ms-auto" />
        </template>

        <template #default="{ collapsed }">
          <UDashboardSearchButton :collapsed="collapsed" class="mb-2" @click="goSearch" />

          <UNavigationMenu
            :collapsed="collapsed"
            :items="navItems"
            orientation="vertical"
          />
        </template>

        <template #footer="{ collapsed }">
          <div class="flex flex-col gap-2 w-full">
            <div class="flex items-center justify-between gap-2">
              <UColorModeSelect size="xs" />
              <UButton
                v-if="!collapsed"
                color="neutral"
                variant="ghost"
                size="xs"
                icon="i-lucide-log-out"
                @click="signOut"
              >
                Sign out
              </UButton>
            </div>
            <p v-if="!collapsed" class="text-[11px] text-muted leading-tight">
              <a href="https://ytmusicapi.readthedocs.io/" target="_blank" rel="noreferrer" class="hover:underline">ytmusicapi</a>
              ·
              <a href="https://spotdl.readthedocs.io/" target="_blank" rel="noreferrer" class="hover:underline">spotDL</a>
              ·
              <a href="https://mediaarea.net/en/MediaInfo" target="_blank" rel="noreferrer" class="hover:underline">MediaInfo</a>
              ·
              <a href="https://ui.nuxt.com/" target="_blank" rel="noreferrer" class="hover:underline">Nuxt UI</a>
            </p>
          </div>
        </template>
      </UDashboardSidebar>

      <RouterView />
    </UDashboardGroup>
  </UApp>
</template>
