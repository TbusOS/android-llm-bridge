/**
 * Render a v3 page inside a memory router and a fresh query client, so
 * pages that use <Link>, useNavigate or useSearch can be tested alone.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

export async function renderPage(ui: ReactElement, path = "/page") {
  const root = createRootRoute({ component: () => <Outlet /> });
  const page = createRoute({ getParentRoute: () => root, path: "$", component: () => ui });
  const router = createRouter({ routeTree: root.addChildren([page]), history: createMemoryHistory({ initialEntries: [path] }) });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const out = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router as never} />
    </QueryClientProvider>,
  );
  await router.load();
  return { ...out, router };
}

/** fetch stub answering by URL prefix; anything unlisted gets a 404. */
export function stubFetch(routes: Record<string, unknown>) {
  return async (input: RequestInfo | URL): Promise<Response> => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^https?:\/\/[^/]+/, "");
    const key = Object.keys(routes)
      .sort((a, b) => b.length - a.length)
      .find((k) => path.startsWith(k));
    if (key === undefined) return new Response(JSON.stringify({ detail: "not stubbed" }), { status: 404, headers: { "content-type": "application/json" } });
    return new Response(JSON.stringify(routes[key]), { status: 200, headers: { "content-type": "application/json" } });
  };
}
