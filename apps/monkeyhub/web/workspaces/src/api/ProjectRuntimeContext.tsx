import { createContext, useContext, useMemo, type ReactNode } from "react";
import { createStudioClient, type StudioClient } from "./client";
import { ServerConnection } from "./connection";

interface ProjectRuntime {
  connection: ServerConnection;
  studio: StudioClient;
}

const ProjectRuntimeContext = createContext<ProjectRuntime | null>(null);

/** Each mounted Hub project retains its own runtime, including pending requests. */
export function ProjectRuntimeProvider({ baseUrl, token = null, children }: {
  baseUrl: string;
  token?: string | null;
  children: ReactNode;
}) {
  const runtime = useMemo(() => {
    const connection = new ServerConnection(baseUrl, token);
    return { connection, studio: createStudioClient(connection) };
  }, [baseUrl, token]);
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
