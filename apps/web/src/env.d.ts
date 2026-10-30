// Environment variables read by the web server (documented in apps/web/README.md).
declare namespace NodeJS {
  interface ProcessEnv {
    readonly AQUA_API_URL?: string;
    readonly NEXT_PUBLIC_API_URL?: string;
    readonly FEATURE_DEMO?: string;
    readonly FEATURE_TILES?: string;
    readonly COOKIE_INSECURE?: string;
    // Tests / CI
    readonly CI?: string;
    readonly E2E_PORT?: string;
    readonly E2E_BASE_URL?: string;
    readonly E2E_MOCKED_PORT?: string;
    readonly E2E_CONSOLE_PORT?: string;
    readonly MOCK_API_PORT?: string;
    readonly SCREENSHOT_DIR?: string;
  }
}
