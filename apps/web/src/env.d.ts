// Environment variables read by the web server (documented in apps/web/README.md).
declare namespace NodeJS {
  interface ProcessEnv {
    readonly AQUA_API_URL?: string;
    readonly NEXT_PUBLIC_API_URL?: string;
    readonly FEATURE_DEMO?: string;
    readonly FEATURE_TILES?: string;
    readonly COOKIE_INSECURE?: string;
  }
}
