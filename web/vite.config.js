import {defineConfig} from 'vite';

export default defineConfig({
  base: './',
  build: {sourcemap: false, target: 'es2022'},
});
