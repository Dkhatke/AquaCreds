export interface TokenSet {
  accessToken: string;
  refreshToken: string;
}

/**
 * Where tokens live. Methods may be async so apps can back it with secure storage
 * (e.g. expo-secure-store on mobile, memory + httpOnly-cookie BFF on web).
 */
export interface TokenStore {
  get(): TokenSet | null | Promise<TokenSet | null>;
  set(tokens: TokenSet): void | Promise<void>;
  clear(): void | Promise<void>;
}

/** In-memory store (default). Tokens are lost on reload. */
export function memoryTokenStore(initial: TokenSet | null = null): TokenStore {
  let current = initial;
  return {
    get: () => current,
    set: (tokens) => {
      current = tokens;
    },
    clear: () => {
      current = null;
    },
  };
}
