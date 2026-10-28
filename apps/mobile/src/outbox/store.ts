/**
 * SQLite persistence for the outbox and the offline cache. Written against a minimal `SqlDb`
 * interface that expo-sqlite's `SQLiteDatabase` satisfies; tests run the same SQL on node:sqlite.
 */
import { type OutboxEvent, transition } from "./machine";
import type { NewOutboxItem, OutboxItem, OutboxKind, OutboxStatus, ServerResult } from "./types";

export type SqlValue = string | number | null;

export interface SqlDb {
  execAsync(sql: string): Promise<void>;
  runAsync(sql: string, params: SqlValue[]): Promise<unknown>;
  getAllAsync<T>(sql: string, params: SqlValue[]): Promise<T[]>;
  getFirstAsync<T>(sql: string, params: SqlValue[]): Promise<T | null>;
}

const MIGRATIONS: string[] = [
  `CREATE TABLE IF NOT EXISTS outbox (
     id TEXT PRIMARY KEY NOT NULL,
     kind TEXT NOT NULL CHECK (kind IN ('photo', 'measurement')),
     project_id TEXT NOT NULL,
     period_id TEXT NOT NULL,
     status TEXT NOT NULL,
     payload TEXT NOT NULL,
     attempts INTEGER NOT NULL DEFAULT 0,
     next_attempt_at INTEGER,
     last_error TEXT,
     server_id TEXT,
     result TEXT,
     created_at INTEGER NOT NULL,
     updated_at INTEGER NOT NULL
   );
   CREATE INDEX IF NOT EXISTS outbox_status ON outbox (status, next_attempt_at);
   CREATE TABLE IF NOT EXISTS cache (
     key TEXT PRIMARY KEY NOT NULL,
     value TEXT NOT NULL,
     updated_at INTEGER NOT NULL
   );`,
];

export async function migrate(db: SqlDb): Promise<void> {
  await db.execAsync("PRAGMA journal_mode = WAL;");
  const row = await db.getFirstAsync<{ user_version: number }>("PRAGMA user_version", []);
  const current = row?.user_version ?? 0;
  for (let v = current; v < MIGRATIONS.length; v++) {
    await db.execAsync(`BEGIN; ${MIGRATIONS[v]} PRAGMA user_version = ${v + 1}; COMMIT;`);
  }
}

interface Row {
  id: string;
  kind: OutboxKind;
  project_id: string;
  period_id: string;
  status: OutboxStatus;
  payload: string;
  attempts: number;
  next_attempt_at: number | null;
  last_error: string | null;
  server_id: string | null;
  result: string | null;
  created_at: number;
  updated_at: number;
}

function fromRow(r: Row): OutboxItem {
  const common = {
    id: r.id,
    projectId: r.project_id,
    periodId: r.period_id,
    status: r.status,
    attempts: r.attempts,
    nextAttemptAt: r.next_attempt_at,
    lastError: r.last_error,
    serverId: r.server_id,
    result: r.result ? (JSON.parse(r.result) as ServerResult) : null,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
  const payload: unknown = JSON.parse(r.payload);
  return r.kind === "photo"
    ? {
        ...common,
        kind: "photo",
        payload: payload as Extract<OutboxItem, { kind: "photo" }>["payload"],
      }
    : {
        ...common,
        kind: "measurement",
        payload: payload as Extract<OutboxItem, { kind: "measurement" }>["payload"],
      };
}

export interface OutboxCounts {
  pending: number;
  failed: number;
  awaitingChecks: number;
}

export class OutboxStore {
  constructor(
    private readonly db: SqlDb,
    /** Unique id factory (expo-crypto `randomUUID` in the app). */
    private readonly newId: () => string,
    private readonly now: () => number = Date.now,
  ) {}

  async add(item: NewOutboxItem): Promise<OutboxItem> {
    const now = this.now();
    const id = this.newId();
    await this.db.runAsync(
      `INSERT INTO outbox (id, kind, project_id, period_id, status, payload, attempts,
         created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, 0, ?, ?)`,
      [id, item.kind, item.projectId, item.periodId, JSON.stringify(item.payload), now, now],
    );
    const saved = await this.get(id);
    if (!saved) throw new Error("Outbox insert failed");
    return saved;
  }

  async get(id: string): Promise<OutboxItem | null> {
    const row = await this.db.getFirstAsync<Row>("SELECT * FROM outbox WHERE id = ?", [id]);
    return row ? fromRow(row) : null;
  }

  async list(): Promise<OutboxItem[]> {
    const rows = await this.db.getAllAsync<Row>(
      "SELECT * FROM outbox ORDER BY created_at DESC, id",
      [],
    );
    return rows.map(fromRow);
  }

  /** Items ready to send now, oldest first. */
  async due(now: number): Promise<OutboxItem[]> {
    const rows = await this.db.getAllAsync<Row>(
      `SELECT * FROM outbox
        WHERE status = 'queued' OR (status = 'retry_wait' AND next_attempt_at <= ?)
        ORDER BY created_at, id`,
      [now],
    );
    return rows.map(fromRow);
  }

  async awaitingChecks(): Promise<OutboxItem[]> {
    const rows = await this.db.getAllAsync<Row>(
      "SELECT * FROM outbox WHERE status = 'awaiting_checks' ORDER BY created_at, id",
      [],
    );
    return rows.map(fromRow);
  }

  async counts(): Promise<OutboxCounts> {
    const rows = await this.db.getAllAsync<{ status: OutboxStatus; n: number }>(
      "SELECT status, COUNT(*) AS n FROM outbox GROUP BY status",
      [],
    );
    const by = (s: OutboxStatus) => rows.find((r) => r.status === s)?.n ?? 0;
    return {
      pending: by("queued") + by("sending") + by("retry_wait"),
      failed: by("failed"),
      awaitingChecks: by("awaiting_checks"),
    };
  }

  /** Apply a state-machine event and persist the new state. */
  async apply(item: OutboxItem, event: OutboxEvent): Promise<OutboxItem> {
    const next = transition(item, event, this.now());
    await this.save(next);
    return next;
  }

  private async save(item: OutboxItem): Promise<void> {
    await this.db.runAsync(
      `UPDATE outbox SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?,
         server_id = ?, result = ?, updated_at = ? WHERE id = ?`,
      [
        item.status,
        item.attempts,
        item.nextAttemptAt,
        item.lastError,
        item.serverId,
        item.result ? JSON.stringify(item.result) : null,
        item.updatedAt,
        item.id,
      ],
    );
  }

  /** After a crash or kill, nothing is really "sending": put those items back in the queue. */
  async recoverInterrupted(): Promise<number> {
    const rows = await this.db.getAllAsync<Row>(
      "SELECT * FROM outbox WHERE status = 'sending'",
      [],
    );
    for (const row of rows) await this.apply(fromRow(row), { type: "interrupted" });
    return rows.length;
  }

  async remove(id: string): Promise<void> {
    await this.db.runAsync("DELETE FROM outbox WHERE id = ?", [id]);
  }
}

// ---------------------------------------------------------------- offline cache

export class CacheStore {
  constructor(
    private readonly db: SqlDb,
    private readonly now: () => number = Date.now,
  ) {}

  async get<T>(key: string): Promise<{ value: T; updatedAt: number } | null> {
    const row = await this.db.getFirstAsync<{ value: string; updated_at: number }>(
      "SELECT value, updated_at FROM cache WHERE key = ?",
      [key],
    );
    return row ? { value: JSON.parse(row.value) as T, updatedAt: row.updated_at } : null;
  }

  async set(key: string, value: unknown): Promise<void> {
    await this.db.runAsync(
      `INSERT INTO cache (key, value, updated_at) VALUES (?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
      [key, JSON.stringify(value), this.now()],
    );
  }

  async remove(key: string): Promise<void> {
    await this.db.runAsync("DELETE FROM cache WHERE key = ?", [key]);
  }
}
