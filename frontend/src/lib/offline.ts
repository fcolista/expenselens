import type { ExpenseWrite } from './types';

const DB_NAME = 'expenselens-offline';
const STORE = 'queue';

export interface QueuedExpense {
  id?: number;
  payload: ExpenseWrite;
  created: number;
  error?: string;
}

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await open();
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = action(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

export const queue = {
  add: (payload: ExpenseWrite) => run('readwrite', (store) => store.add({ payload, created: Date.now() })),
  all: () => run<QueuedExpense[]>('readonly', (store) => store.getAll()),
  put: (item: QueuedExpense) => run('readwrite', (store) => store.put(item)),
  remove: (id: number) => run('readwrite', (store) => store.delete(id)),
  clear: () => run('readwrite', (store) => store.clear())
};

const SNAPSHOT_KEY = 'expenselens-snapshot';

export function saveSnapshot(data: unknown): void {
  try { localStorage.setItem(SNAPSHOT_KEY, JSON.stringify(data)); } catch { /* storage full or unavailable */ }
}

export function loadSnapshot<T>(): T | null {
  try { return JSON.parse(localStorage.getItem(SNAPSHOT_KEY) ?? 'null') as T | null; } catch { return null; }
}

export function clearSnapshot(): void {
  try { localStorage.removeItem(SNAPSHOT_KEY); } catch { /* ignore */ }
}
