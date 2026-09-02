import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

// Self-contained demo build: the engine is bundled into the page, so
// `node:crypto` resolves to a browser shim, and everything lands in a single
// JS chunk so the output can be inlined into one shareable HTML file.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      'node:crypto': fileURLToPath(new URL('./src/demo/crypto-shim.ts', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist-demo',
    rollupOptions: {
      input: fileURLToPath(new URL('./demo.html', import.meta.url)),
      output: { inlineDynamicImports: true },
    },
  },
});
