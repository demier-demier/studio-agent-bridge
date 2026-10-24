import {defineConfig} from 'vite';
import {resolve} from 'node:path';

export default defineConfig({
  base: './',
  build: {
    sourcemap: false, target: 'es2022',
    rollupOptions: {input: {main: resolve('index.html'), environment: resolve('environment/index.html'), recovery: resolve('recovery/index.html'), sourceBinding: resolve('source-binding/index.html')}},
  },
});
