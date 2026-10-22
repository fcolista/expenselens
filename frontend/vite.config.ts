import { sveltekit } from '@sveltejs/kit/vite';
import adapter from '@sveltejs/adapter-node';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000'
    }
  },
  plugins: [
    sveltekit({
      adapter: adapter(),
      csp: {
        mode: 'auto',
        directives: {
          'default-src': ['self'],
          'script-src': ['self'],
          'style-src': ['self', 'unsafe-inline'],
          'img-src': ['self', 'data:', 'blob:'],
          'connect-src': ['self'],
          'object-src': ['none'],
          'base-uri': ['self'],
          'frame-ancestors': ['none']
        }
      }
    }),
    tailwindcss()
  ]
});
