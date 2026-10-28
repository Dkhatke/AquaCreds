/**
 * App-wide state: database, API client, session, the chosen project/period, and the outbox sync
 * loop. Sync runs on start, when the app returns to the foreground, when the network comes back,
 * every minute while open, and on demand. (No background task yet: see README.)
 */
import type { AquaClient, ProjectOut, Schema } from "@aquacreds/api-client";
import { randomUUID } from "expo-crypto";
import { useNetworkState } from "expo-network";
import { openDatabaseAsync } from "expo-sqlite";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AppState } from "react-native";
import { createClient } from "../api/client";
import { apiBaseUrl } from "../config";
import { CacheStore, migrate, type OutboxCounts, OutboxStore, type SqlDb } from "../outbox/store";
import { SyncEngine, type SyncSummary } from "../outbox/sync";
import { deleteFile, readFileBytes } from "../platform/files";
import { sha256Hex } from "../platform/sha256";

export type PeriodOut = Schema<"PeriodOut">;
export type PlotOut = Schema<"PlotOut">;

export interface Selection {
  project: Pick<ProjectOut, "id" | "name" | "boundary" | "state">;
  period: PeriodOut;
}

type SessionState = "loading" | "signedOut" | "signedIn";

interface AppContextValue {
  client: AquaClient;
  outbox: OutboxStore;
  cache: CacheStore;
  session: SessionState;
  setSignedIn: () => void;
  signOut: () => Promise<void>;
  selection: Selection | null;
  select: (s: Selection | null) => Promise<void>;
  online: boolean;
  counts: OutboxCounts;
  /** Bumped whenever the outbox changes, so lists can reload. */
  outboxVersion: number;
  refreshOutbox: () => Promise<void>;
  syncing: boolean;
  lastSync: SyncSummary | null;
  syncNow: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside <AppProvider>");
  return ctx;
}

const SYNC_INTERVAL_MS = 60_000;
const SELECTION_KEY = "selection";

interface Core {
  client: AquaClient;
  outbox: OutboxStore;
  cache: CacheStore;
  engine: SyncEngine;
}

async function openCore(baseUrl: string, onLogout: () => void): Promise<Core> {
  const db = await openDatabaseAsync("aquacreds.db");
  const sql: SqlDb = {
    execAsync: (s) => db.execAsync(s),
    runAsync: (s, p) => db.runAsync(s, p),
    getAllAsync: (s, p) => db.getAllAsync(s, p),
    getFirstAsync: (s, p) => db.getFirstAsync(s, p),
  };
  await migrate(sql);
  const outbox = new OutboxStore(sql, randomUUID);
  await outbox.recoverInterrupted();
  const client = createClient(baseUrl, onLogout);
  const engine = new SyncEngine({
    client,
    store: outbox,
    readFile: readFileBytes,
    sha256: sha256Hex,
    deleteFile,
    now: Date.now,
  });
  return { client, outbox, cache: new CacheStore(sql), engine };
}

export function AppProvider({
  children,
  fallback,
}: {
  children: ReactNode;
  fallback: (error: string | null) => ReactNode;
}) {
  const [core, setCore] = useState<Core | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionState>("loading");
  const [selection, setSelection] = useState<Selection | null>(null);
  const [counts, setCounts] = useState<OutboxCounts>({ pending: 0, failed: 0, awaitingChecks: 0 });
  const [outboxVersion, setOutboxVersion] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState<SyncSummary | null>(null);
  const network = useNetworkState();
  const online = network.isConnected === true && network.isInternetReachable !== false;
  const coreRef = useRef<Core | null>(null);

  useEffect(() => {
    const baseUrl = apiBaseUrl();
    if (!baseUrl) {
      setError("config.missingApiUrl");
      return;
    }
    let cancelled = false;
    openCore(baseUrl, () => setSession("signedOut"))
      .then(async (c) => {
        if (cancelled) return;
        coreRef.current = c;
        const tokens = await c.client.tokens.get();
        const saved = await c.cache.get<Selection>(SELECTION_KEY);
        setSelection(saved?.value ?? null);
        setCounts(await c.outbox.counts());
        // Offline-first: stored tokens count as signed in; the API decides when we are online.
        setSession(tokens ? "signedIn" : "signedOut");
        setCore(c);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshOutbox = useCallback(async () => {
    const c = coreRef.current;
    if (!c) return;
    setCounts(await c.outbox.counts());
    setOutboxVersion((v) => v + 1);
  }, []);

  const syncNow = useCallback(async () => {
    const c = coreRef.current;
    if (!c || session !== "signedIn") return;
    setSyncing(true);
    try {
      const summary = await c.engine.sync();
      setLastSync(summary);
      if (summary.needsSignIn) setSession("signedOut");
    } finally {
      setSyncing(false);
      await refreshOutbox();
    }
  }, [session, refreshOutbox]);

  // Triggers: start / sign-in, network regained, foreground, interval.
  useEffect(() => {
    if (!core || session !== "signedIn" || !online) return;
    void syncNow();
    const timer = setInterval(() => void syncNow(), SYNC_INTERVAL_MS);
    const sub = AppState.addEventListener("change", (s) => {
      if (s === "active") void syncNow();
    });
    return () => {
      clearInterval(timer);
      sub.remove();
    };
  }, [core, session, online, syncNow]);

  const select = useCallback(async (s: Selection | null) => {
    const c = coreRef.current;
    setSelection(s);
    if (!c) return;
    if (s) await c.cache.set(SELECTION_KEY, s);
    else await c.cache.remove(SELECTION_KEY);
  }, []);

  const signOut = useCallback(async () => {
    const c = coreRef.current;
    if (!c) return;
    // Queued evidence stays on the phone and syncs after the next sign-in.
    await c.client.auth.logout();
    setSession("signedOut");
  }, []);

  const value = useMemo<AppContextValue | null>(
    () =>
      core && {
        client: core.client,
        outbox: core.outbox,
        cache: core.cache,
        session,
        setSignedIn: () => setSession("signedIn"),
        signOut,
        selection,
        select,
        online,
        counts,
        outboxVersion,
        refreshOutbox,
        syncing,
        lastSync,
        syncNow,
      },
    [
      core,
      session,
      signOut,
      selection,
      select,
      online,
      counts,
      outboxVersion,
      refreshOutbox,
      syncing,
      lastSync,
      syncNow,
    ],
  );

  if (!value) return <>{fallback(error)}</>;
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
