import { defineWorkersConfig } from '@cloudflare/vitest-pool-workers/config';

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: './wrangler.toml' },
        miniflare: {
          bindings: {
            GITHUB_TOKEN: 'test-token-for-unit-tests',
            GITHUB_REPO: 'test-org/test-repo'
          }
        }
      }
    }
  }
});
