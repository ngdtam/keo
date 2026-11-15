<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { TableColumn } from '@nuxt/ui'
import { apiGet, apiPostForm } from '../api'

interface OrganizeMove {
  artist: string
  title: string
  src: string
  dst: string
}

interface OrganizePreviewResponse {
  template: string
  moves: OrganizeMove[]
}

const router = useRouter()
const toast = useToast()

const template = ref('')
const moves = ref<OrganizeMove[]>([])
const error = ref('')
const loading = ref(false)
const applying = ref(false)

const columns: TableColumn<OrganizeMove>[] = [
  { id: 'song', header: 'Song' },
  { id: 'change', header: 'Path change' },
]

async function loadPreview() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiGet<OrganizePreviewResponse>('/api/library/organize/preview')
    template.value = res.template
    moves.value = res.moves
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load organize preview'
  } finally {
    loading.value = false
  }
}

async function applyRenames() {
  applying.value = true
  try {
    await apiPostForm('/library/organize')
    toast.add({ title: 'Renames applied', color: 'success' })
    await router.push('/library')
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Failed to organize library',
      color: 'error',
    })
  } finally {
    applying.value = false
  }
}

onMounted(loadPreview)
</script>

<template>
  <UDashboardPanel id="organize-preview">
    <template #header>
      <UDashboardNavbar title="Preview rename">
        <template #right>
          <div class="flex items-center gap-2">
            <UButton to="/library" variant="outline" color="neutral" size="sm" icon="i-lucide-arrow-left">
              Back to Library
            </UButton>
            <UButton
              v-if="moves.length > 0"
              size="sm"
              color="primary"
              icon="i-lucide-check"
              :loading="applying"
              @click="applyRenames"
            >
              Apply renames ({{ moves.length }})
            </UButton>
          </div>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <UCard>
          <template v-if="template" #header>
            <p class="text-xs text-muted">
              Template: <code class="font-mono">{{ template }}</code>
            </p>
          </template>

          <div v-if="!loading && moves.length === 0" class="py-8 text-center text-sm text-muted">
            All files already match the template.
          </div>

          <UTable v-else :data="moves" :columns="columns" :loading="loading">
            <template #song-cell="{ row }">
              <span class="font-medium">{{ row.original.artist }} - {{ row.original.title }}</span>
            </template>

            <template #change-cell="{ row }">
              <div class="space-y-0.5 font-mono text-xs">
                <div class="text-error">- {{ row.original.src }}</div>
                <div class="text-success">+ {{ row.original.dst }}</div>
              </div>
            </template>
          </UTable>
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
