import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'

const project = process.env.MOMO_PROJECT
const runner = process.env.MOMO_RUNNER

if (!project) throw new Error('MOMO_PROJECT is required')
if (!runner) throw new Error('MOMO_RUNNER is required')

function required(value, name) {
  if (value === undefined || value === null || value === '') {
    throw new Error(`config/app.json 缺少配置项: ${name}`)
  }
  return value
}

const appConfig = JSON.parse(fs.readFileSync(path.join(project, 'config', 'app.json'), 'utf-8'))
const backendHost = required(appConfig.server?.backend?.host, 'server.backend.host')
const backendPort = Number(required(appConfig.server?.backend?.port, 'server.backend.port'))
const frontendHost = required(appConfig.server?.frontend?.host, 'server.frontend.host')
const frontendPort = Number(required(appConfig.server?.frontend?.port, 'server.frontend.port'))

const nm = path.join(runner, 'node_modules')
const requireFromRunner = createRequire(path.join(runner, 'package.json'))
const { defineConfig } = requireFromRunner('vite')
const vueModule = requireFromRunner('@vitejs/plugin-vue')
const vue = vueModule.default || vueModule

export default defineConfig({
  root: project,
  plugins: [vue()],
  cacheDir: path.join(process.env.HOME || runner, '.cache/momo-vite'),
  resolve: {
    preserveSymlinks: true,
    alias: {
      '@': path.join(project, 'src'),
      vue: path.join(nm, 'vue'),
      pinia: path.join(nm, 'pinia'),
      'element-plus': path.join(nm, 'element-plus'),
      '@element-plus/icons-vue': path.join(nm, '@element-plus/icons-vue'),
      echarts: path.join(nm, 'echarts'),
      'plotly.js-dist-min': path.join(nm, 'plotly.js-dist-min'),
    },
    dedupe: [
      'vue',
      'pinia',
      'element-plus',
      '@element-plus/icons-vue',
      'echarts',
      'plotly.js-dist-min',
    ],
  },
  optimizeDeps: {
    include: [
      'vue',
      'pinia',
      'element-plus',
      '@element-plus/icons-vue',
      'echarts',
      'plotly.js-dist-min',
    ],
  },
  server: {
    host: frontendHost,
    port: frontendPort,
    fs: {
      allow: [project, runner, nm],
    },
    proxy: {
      '/api': {
        target: `http://${backendHost}:${backendPort}`,
        changeOrigin: true,
      },
    },
  },
})
