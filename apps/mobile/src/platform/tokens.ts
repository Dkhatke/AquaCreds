import type { TokenSet, TokenStore } from "@aquacreds/api-client";
import * as SecureStore from "expo-secure-store";

const ACCESS = "aquacreds.access";
const REFRESH = "aquacreds.refresh";

// Keychain / Android Keystore-backed. Available only after the first unlock; never synced to backups.
const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
};

/** Token store for the API client, backed by expo-secure-store (never AsyncStorage/SQLite). */
export function secureTokenStore(): TokenStore {
  let cache: TokenSet | null | undefined;
  return {
    async get() {
      if (cache !== undefined) return cache;
      const [accessToken, refreshToken] = await Promise.all([
        SecureStore.getItemAsync(ACCESS, OPTIONS),
        SecureStore.getItemAsync(REFRESH, OPTIONS),
      ]);
      cache = accessToken && refreshToken ? { accessToken, refreshToken } : null;
      return cache;
    },
    async set(tokens) {
      cache = tokens;
      await Promise.all([
        SecureStore.setItemAsync(ACCESS, tokens.accessToken, OPTIONS),
        SecureStore.setItemAsync(REFRESH, tokens.refreshToken, OPTIONS),
      ]);
    },
    async clear() {
      cache = null;
      await Promise.all([
        SecureStore.deleteItemAsync(ACCESS, OPTIONS),
        SecureStore.deleteItemAsync(REFRESH, OPTIONS),
      ]);
    },
  };
}
