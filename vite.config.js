import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'url'
import fs from 'fs'

const projectRoot = fileURLToPath(new URL('.', import.meta.url))

function required(value, name) {
  if (value === undefined || value === null || value === '') {
    throw new Error(`app.json 缺少配置项: ${name}`)
  }
  return value
}

const configUrl = new URL('./app.json', import.meta.url)
const appConfig = JSON.parse(fs.readFileSync(configUrl, 'utf-8'))
const backendHost = process.env.BACKEND_HOST || required(appConfig.server?.backend?.host, 'server.backend.host')
const backendPort = Number(process.env.BACKEND_PORT || required(appConfig.server?.backend?.port, 'server.backend.port'))
const frontendHost = process.env.FRONTEND_HOST || required(appConfig.server?.frontend?.host, 'server.frontend.host')
const frontendPort = Number(process.env.FRONTEND_PORT || required(appConfig.server?.frontend?.port, 'server.frontend.port'))

export default defineConfig({
  root: projectRoot,
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    host: frontendHost,
    port: frontendPort,
    proxy: {
      '/api': `http://${backendHost}:${backendPort}`,
    }
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    rollupOptions: {
      input: fileURLToPath(new URL('./index.html', import.meta.url)),
    },
  }
})
