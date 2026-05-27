import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'url'
import fs from 'fs'

function required(value, name) {
  if (value === undefined || value === null || value === '') {
    throw new Error(`config/app.json 缺少配置项: ${name}`)
  }
  return value
}

const configUrl = new URL('./config/app.json', import.meta.url)
const appConfig = JSON.parse(fs.readFileSync(configUrl, 'utf-8'))
const backendHost = required(appConfig.server?.backend?.host, 'server.backend.host')
const backendPort = Number(required(appConfig.server?.backend?.port, 'server.backend.port'))
const frontendHost = required(appConfig.server?.frontend?.host, 'server.frontend.host')
const frontendPort = Number(required(appConfig.server?.frontend?.port, 'server.frontend.port'))

export default defineConfig({
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
  }
})
