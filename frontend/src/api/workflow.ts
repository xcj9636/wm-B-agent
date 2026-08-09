import api from './index'
import type { Workflow, WorkflowCreate, WorkflowUpdate, Execution } from '@/types'

export const workflowApi = {
  async list() {
    const response = await api.get<Workflow[]>('/api/v1/workflows')
    return response.data
  },

  async get(id: string) {
    const response = await api.get<Workflow>(`/api/v1/workflows/${id}`)
    return response.data
  },

  async create(data: WorkflowCreate) {
    const response = await api.post<Workflow>('/api/v1/workflows', data)
    return response.data
  },

  async update(id: string, data: WorkflowUpdate) {
    const response = await api.put<Workflow>(`/api/v1/workflows/${id}`, data)
    return response.data
  },

  async delete(id: string) {
    await api.delete(`/api/v1/workflows/${id}`)
  },

  async execute(id: string, inputData: Record<string, any>) {
    const baseUrl = String(api.defaults.baseURL || '').replace(/\/$/, '')
    const token = localStorage.getItem('access_token')

    return new Promise<{ execution_id: string }>((resolve, reject) => {
      let settled = false

      void fetch(`${baseUrl}/api/v1/workflows/${id}/execute`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ workflow_id: id, input_data: inputData }),
      }).then(async (response) => {
        if (!response.ok || !response.body) {
          throw new Error(`Workflow execution failed with HTTP ${response.status}`)
        }

        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          buffer += decoder.decode(value, { stream: !done })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (!line.startsWith('data: ')) continue
            let event: { execution_id?: string; error?: string }
            try {
              event = JSON.parse(line.slice(6)) as { execution_id?: string; error?: string }
            } catch {
              continue
            }
            if (event.execution_id && !settled) {
              settled = true
              resolve({ execution_id: event.execution_id })
            }
            if (event.error && !settled) throw new Error(event.error)
          }

          if (done) break
        }

        if (!settled) throw new Error('Execution started without an execution identifier')
      }).catch((error: unknown) => {
        if (!settled) reject(error)
      })
    })
  },

  async getExecution(id: string) {
    const response = await api.get<Execution>(`/api/v1/workflows/executions/${id}`)
    return response.data
  },

  async pauseExecution(id: string) {
    await api.post(`/api/v1/workflows/executions/${id}/pause`)
  },

  async resumeExecution(id: string) {
    await api.post(`/api/v1/workflows/executions/${id}/resume`)
  },

  async cancelExecution(id: string) {
    await api.post(`/api/v1/workflows/executions/${id}/cancel`)
  },
}
