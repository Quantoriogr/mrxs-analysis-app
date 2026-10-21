import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [
      'react', 'react/jsx-runtime', 'react/jsx-dev-runtime', 'react-dom', 'openseadragon',
      '@testing-library/react', '@testing-library/jest-dom/vitest', 'vitest',
    ].map(name => ({ find: new RegExp('^' + name + '$'), replacement: fileURLToPath(import.meta.resolve(name)) })),
  },
  test: {
    root: fileURLToPath(new URL('../', import.meta.url)),
    environment: 'jsdom',
    include: ['tests/frontend/**/*.test.{ts,tsx}'],
    setupFiles: ['tests/frontend/setup.ts'],
    clearMocks: true,
  },
});
