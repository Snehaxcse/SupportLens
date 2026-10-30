import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120000,
  expect: { timeout: 15000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "../artifacts/m3/playwright_results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:3100",
    browserName: "chromium",
    channel: "chrome",
    trace: "off",
    video: "off",
    screenshot: "off",
  },
  projects: [
    { name: "desktop", use: { viewport: { width: 1440, height: 1000 } } },
    { name: "tablet", use: { viewport: { width: 900, height: 1000 } } },
    {
      name: "mobile",
      use: {
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3100",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: false,
    timeout: 180000,
    env: {
      NEXT_TELEMETRY_DISABLED: "1",
      SUPPORTLENS_BUILD_CONTEXT: "e2e",
      NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8900",
      NEXT_PUBLIC_FIREBASE_PROJECT_ID: "synthetic-supportlens",
      NEXT_PUBLIC_FIREBASE_API_KEY: "synthetic-web-api-key",
      NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: "",
      NEXT_PUBLIC_FIREBASE_APP_ID: "",
    },
  },
});
