import api from './index'
import type { Skill } from '@/types'

interface SkillResponse {
  name: string
  display_name: string
  description: string
  category: string
  version: string
  config_schema: Record<string, any>
  input_schema: Record<string, any>
  output_schema: Record<string, any>
}

function normalizeSkill(skill: SkillResponse): Skill {
  return {
    id: skill.name,
    name: skill.name,
    displayName: skill.display_name,
    description: skill.description,
    category: skill.category,
    version: skill.version,
    inputSchema: skill.input_schema,
    outputSchema: skill.output_schema,
    configTemplate: skill.config_schema,
    enabled: true,
  }
}

export const skillApi = {
  async list() {
    const response = await api.get<{ skills: SkillResponse[]; categories: string[] }>(
      '/api/v1/skills'
    )
    return {
      skills: response.data.skills.map(normalizeSkill),
      categories: response.data.categories,
    }
  },

  async get(name: string) {
    const response = await api.get<SkillResponse>(`/api/v1/skills/${name}`)
    return normalizeSkill(response.data)
  },
}
