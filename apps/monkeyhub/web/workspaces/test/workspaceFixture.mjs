import { readFile } from "node:fs/promises";

/** Existing browser fixtures keep their root URL and isolated API handlers. */
export function workspaceFixture() {
  return {
    name: "project-workspace-test-entry",
    configureServer(server) {
      server.middlewares.use(async (request, response, next) => {
        if (new URL(request.url, "http://fixture.test").pathname !== "/") return next();
        try {
          const html = await readFile(new URL("./workspace.html", import.meta.url), "utf8");
          response.setHeader("Content-Type", "text/html");
          response.end(await server.transformIndexHtml("/test/workspace.html", html));
        } catch (error) { next(error); }
      });
    },
  };
}
