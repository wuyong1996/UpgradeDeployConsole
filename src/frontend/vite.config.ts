import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  build: { outDir: '../DeployConsole.Api/wwwroot', emptyOutDir: true },
  server: { proxy: { '/api': { target: 'http://127.0.0.1:5088', changeOrigin: false }, '/health': 'http://127.0.0.1:5088' } },
});
