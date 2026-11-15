import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import ui from '@nuxt/ui/vite'

export default defineConfig({
  plugins: [
    vue(),
    ui({
      ui: {
        colors: {
          primary: 'amber',
          neutral: 'stone',
        },
      },
    }),
  ],
  build: {
    outDir: '../src/keo/web/ui_dist',
    emptyOutDir: true,
  },
})
