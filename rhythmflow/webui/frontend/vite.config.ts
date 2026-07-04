import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The window is served by the Python media server (or Vite in dev). Relative
// asset paths keep the bundle portable when loaded over http://127.0.0.1.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    outDir: '../frontend_dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    strictPort: true,
  },
});
