import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: {
    rolldownOptions: {
      output: {
        codeSplitting: {
          groups: [
            { name: 'react-vendor', test: /node_modules[\\/](react|react-dom|react-router)/, priority: 30 },
            { name: 'charts-vendor', test: /node_modules[\\/](recharts|d3-|victory-vendor)/, priority: 20 },
            { name: 'icons-vendor', test: /node_modules[\\/]lucide-react/, priority: 15 },
            { name: 'vendor', test: /node_modules/, priority: 10, maxSize: 300000 },
          ],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
