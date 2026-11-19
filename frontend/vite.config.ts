import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  ...(mode === 'desktop' && {
    build: { outDir: 'dist-desktop' },
    define: { 'import.meta.env.VITE_API_BASE_URL': JSON.stringify('/') },
  }),
  server: {
    host: '127.0.0.1',
    port: 15173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:18000',
        changeOrigin: true,
      },
    },
  },
}));
