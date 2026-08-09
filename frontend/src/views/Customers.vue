<template>
  <div class="customers page-stack">
    <header class="page-heading">
      <div>
        <p class="page-kicker">
          {{ $t('Relationships') }}
        </p>
        <h1>{{ $t('Customers') }}</h1>
        <p>{{ $t('Review customer identity, reach, lifecycle state and engagement context.') }}</p>
      </div>
      <el-button
        type="primary"
        disabled
        :title="$t('Customer import is not enabled by the backend')"
      >
        <el-icon><Upload /></el-icon>
        {{ $t('Import') }}
      </el-button>
    </header>

    <el-card shadow="never">
      <el-table
        v-loading="loading"
        :data="customers"
      >
        <el-table-column
          prop="username"
          :label="$t('Username')"
        />
        <el-table-column
          prop="platform"
          :label="$t('Platform')"
        />
        <el-table-column
          prop="email"
          :label="$t('Email')"
        />
        <el-table-column
          prop="country"
          :label="$t('Country')"
        />
        <el-table-column
          prop="follower_count"
          :label="$t('Followers')"
        />
        <el-table-column
          prop="status"
          :label="$t('Status')"
        >
          <template #default="{ row }">
            <el-tag :type="getStatusType(row.status)">
              {{ $t(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          :label="$t('Actions')"
          width="150"
        >
          <template #default="{ row }">
            <el-button
              text
              @click="viewCustomer(row.id)"
            >
              {{ $t('View') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { customerApi } from '@/api/customer'

const router = useRouter()

const loading = ref(false)
const customers = ref<any[]>([])

async function fetchCustomers() {
  loading.value = true
  try {
    const data = await customerApi.list({ page: 1, page_size: 50 })
    customers.value = data.items
  } finally {
    loading.value = false
  }
}

function viewCustomer(id: number) {
  router.push(`/customers/${id}`)
}

function getStatusType(status: string) {
  const types: Record<string, any> = {
    new: 'info',
    contacted: 'primary',
    engaged: 'warning',
    converted: 'success',
    lost: 'danger',
  }
  return types[status] || 'info'
}

onMounted(() => {
  fetchCustomers()
})
</script>
