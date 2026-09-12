import { defineConfig } from 'vite';

export default defineConfig({
  build: {
    // Бандл кладём прямо в js/ рядом с остальной статикой; emptyOutDir=false
    // обязателен — иначе Vite очистит весь js/. Sourcemap выключен: иначе
    // .map попадёт в js/ и в поле зрения tools/stamp_assets.py.
    outDir: 'js',
    emptyOutDir: false,
    sourcemap: false,
    rollupOptions: {
      input: 'frontend/nakladnye.js',
      output: {
        format: 'es',
        entryFileNames: 'nakladnye.bundle.js',
      },
    },
  },
});
