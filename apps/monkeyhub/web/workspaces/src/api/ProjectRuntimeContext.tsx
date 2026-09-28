import { createContext, useContext, useEffect, useMemo, useSyncExternalStore, type ReactNode } from "react";
import { createStudioClient, type StudioClient } from "./client";
import { ServerConnection } from "./connection";
import { projectStores, type IndexAnswer, type IndexReader, type ProjectStore, type ProjectStoreState } from "./projectStore";

interface ProjectRuntime {
  connection: ServerConnection;
  studio: StudioClient;
  /** What the Hub's event stream calls this project; null outside the Hub. */
  runtimeKey: string | null;
  store: ProjectStore;
}

const ProjectRuntimeContext = createContext<ProjectRuntime | null>(null);

/** `GET /api/index`: the changes since the store's revision, or a snapshot; null while the index cannot answer. */
function indexReader(connection: ServerConnection): IndexReader {
  return async (since) => {
    const query = since === null ? "" : `?${new URLSearchParams({ since: String(since.revision), epoch: since.epoch })}`;
    const response = await fetch(connection.url(`/api/index${query}`), { cache: "no-store" });
    if (!response.ok) return null;
    return await response.json() as IndexAnswer;
  };
}

/**
 * Each mounted Hub project retains its own runtime, including pending requests,
 * and shares its project store with every other surface of that project (#366).
 */
export function ProjectRuntimeProvider({ baseUrl, token = null, runtimeId = null, children }: {
  baseUrl: string;
  token?: string | null;
  /** The Hub runtime this project runs in: the key its store's hints arrive under. */
  runtimeId?: string | null;
  children: ReactNode;
}) {
  const key = runtimeId ?? baseUrl;
  const runtime = useMemo(() => {
    const connection = new ServerConnection(baseUrl, token);
    const store = projectStores.store(key, indexReader(connection));
    // Without a Hub stream nothing keeps the store current, so a write waits for nothing.
    connection.onIndexWrite = (epoch, revision) => { if (projectStores.attached) void store.wrote(epoch, revision); };
    return { connection, studio: createStudioClient(connection), runtimeKey: runtimeId, store };
  }, [baseUrl, token, key, runtimeId]);
  useEffect(() => {
    projectStores.acquire(key, indexReader(runtime.connection));
    return () => projectStores.release(key);
  }, [runtime, key]);
  return <ProjectRuntimeContext.Provider value={runtime}>{children}</ProjectRuntimeContext.Provider>;
}

function useProjectRuntime(): ProjectRuntime {
  const runtime = useContext(ProjectRuntimeContext);
  if (runtime === null) throw new Error("A project runtime provider is required for this workspace.");
  return runtime;
}

export function useStudio(): StudioClient {
  return useProjectRuntime().studio;
}

export function useConnection(): ServerConnection {
  return useProjectRuntime().connection;
}

/** This project's store (#366), shared by its surfaces. */
export function useProjectStoreInstance(): ProjectStore {
  return useProjectRuntime().store;
}

/** What the Hub's event stream calls this project, or null outside the Hub. */
export function useRuntimeKey(): string | null {
  return useProjectRuntime().runtimeKey;
}

/** A value selected from this project's store; the selector must return a primitive or a kept object. */
export function useProjectStore<T>(selector: (state: ProjectStoreState) => T): T {
  const store = useProjectRuntime().store;
  return useSyncExternalStore(store.subscribe, () => selector(store.getSnapshot()));
}

/**
 * Where the project index stands, as one key (`<epoch>:<revision>`), or null
 * before the store has read it (outside the Hub it never does). A surface
 * reads its views again when this moves, and never on a timer.
 */
export function useProjectRevision(): string | null {
  return useProjectStore((state) => state.epoch === null ? null : `${state.epoch}:${state.revision}`);
}
