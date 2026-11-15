<script setup lang="ts">
import { onMounted, ref } from 'vue'
import type { TableColumn } from '@nuxt/ui'
import { apiGet, apiPostForm } from '../api'

interface TrashItem {
  name: string
  size: number
  size_label: string
  deleted_at: string
}

interface TrashResponse {
  recycle_days: number
  items: TrashItem[]
}

const toast = useToast()

const recycleDays = ref(0)
const items = ref<TrashItem[]>([])
const error = ref('')
const loading = ref(false)
const busy = ref('')

const columns: TableColumn<TrashItem>[] = [
  { accessorKey: 'name', header: 'File' },
  { accessorKey: 'size_label', header: 'Size' },
  { accessorKey: 'deleted_at', header: 'Deleted' },
  { id: 'actions', header: '' },
]

async function loadTrash() {
  loading.value = true
  error.value = ''
  try {
    const res = await apiGet<TrashResponse>('/api/library/trash')
    recycleDays.value = res.recycle_days
    items.value = res.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Failed to load trash'
  } finally {
    loading.value = false
  }
}

async function restoreItem(name: string) {
  busy.value = `restore:${name}`
  try {
    await apiPostForm('/library/trash/restore', { name })
    toast.add({ title: `Restored ${name}`, color: 'success' })
    await loadTrash()
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Restore failed',
      color: 'error',
    })
  } finally {
    busy.value = ''
  }
}

async function deleteItem(name: string) {
  busy.value = `delete:${name}`
  try {
    await apiPostForm('/library/trash/delete', { name })
    toast.add({ title: `Deleted ${name}`, color: 'success' })
    await loadTrash()
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Delete failed',
      color: 'error',
    })
  } finally {
    busy.value = ''
  }
}

async function emptyTrash() {
  busy.value = 'empty'
  try {
    await apiPostForm('/library/trash/empty')
    toast.add({ title: 'Trash emptied', color: 'success' })
    await loadTrash()
  } catch (err) {
    toast.add({
      title: err instanceof Error ? err.message : 'Failed to empty trash',
      color: 'error',
    })
  } finally {
    busy.value = ''
  }
}

onMounted(loadTrash)
</script>

<template>
  <UDashboardPanel id="trash">
    <template #header>
      <UDashboardNavbar title="Trash">
        <template #right>
          <div class="flex items-center gap-2">
            <UButton to="/library" variant="outline" color="neutral" size="sm" icon="i-lucide-arrow-left">
              Back to Library
            </UButton>
            <UButton
              v-if="items.length > 0"
              color="error"
              variant="outline"
              size="sm"
              icon="i-lucide-trash-2"
              :loading="busy === 'empty'"
              @click="emptyTrash"
            >
              Empty trash
            </UButton>
          </div>
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <div class="flex flex-col gap-4">
        <UAlert v-if="error" color="error" variant="subtle" :description="error" />

        <p class="text-sm text-muted">
          <template v-if="recycleDays > 0">
            Deleted files are kept for {{ recycleDays }} days before automatic removal.
          </template>
          <template v-else>
            Automatic trash cleanup is disabled.
          </template>
        </p>

        <UCard>
          <div v-if="!loading && items.length === 0" class="py-8 text-center text-sm text-muted">
            Trash is empty.
          </div>

          <UTable v-else :data="items" :columns="columns" :loading="loading">
            <template #actions-cell="{ row }">
              <div class="flex justify-end gap-2">
                <UButton
                  size="xs"
                  variant="outline"
                  color="neutral"
                  :loading="busy === `restore:${row.original.name}`"
                  @click="restoreItem(row.original.name)"
                >
                  Restore
                </UButton>
                <UButton
                  size="xs"
                  color="error"
                  variant="outline"
                  :loading="busy === `delete:${row.original.name}`"
                  @click="deleteItem(row.original.name)"
                >
                  Delete permanently
                </UButton>
              </div>
            </template>
          </UTable>
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
