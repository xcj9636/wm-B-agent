import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

export const useThemeStore = defineStore('theme', () => {
  const isDark = ref(false)
  const initialized = ref(false)

  function applySavedPreference() {
    const saved = localStorage.getItem('theme') || 'auto'
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    isDark.value = saved === 'dark' || (saved === 'auto' && prefersDark)
  }

  function toggleTheme() {
    setTheme(!isDark.value)
  }

  function setTheme(dark: boolean) {
    isDark.value = dark
    localStorage.setItem('theme', dark ? 'dark' : 'light')
  }

  function useSystemTheme() {
    localStorage.setItem('theme', 'auto')
    applySavedPreference()
  }

  function initialize() {
    if (initialized.value) return
    initialized.value = true
    applySavedPreference()

    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', (event) => {
      if ((localStorage.getItem('theme') || 'auto') === 'auto') {
        isDark.value = event.matches
      }
    })
  }

  watch(
    isDark,
    (dark) => {
      document.documentElement.classList.toggle('dark', dark)
    },
    { immediate: true }
  )

  return {
    isDark,
    toggleTheme,
    setTheme,
    useSystemTheme,
    initialize,
  }
})
