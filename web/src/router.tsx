/**
 * Router assembly — code-based TanStack Router setup with the
 * RootLayout wrapping every route.  v2 module map (8 entries on the
 * activity bar): Dashboard / Chat / Terminal / Inspect / Playground /
 * Sessions / Files / Audit.  All 8 are real now; Terminal and Files
 * are thin redirects to the matching Inspect tab.
 *
 * Cross-repo invariant (DEBT-014, 2026-04-29): every `path:` value
 * below MUST NOT contain `.` in any segment. The backend SPA fallback
 * (`src/alb/api/ui_static.py SPAStaticFiles`) uses presence of `.` in
 * the last path segment to distinguish "missing asset" from "SPA
 * route", so a route like `/app/v2.0/foo` would be misclassified as
 * an asset 404. See `.claude/knowledge/architecture.md` 关键不变量.
 */
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { lazy } from "react";
import { ChatPage } from "./features/chat/ChatPage";
import { DashboardPage } from "./features/dashboard/DashboardPage";
import { AuditPage } from "./features/audit/AuditPage";
import { ConnectionsPage } from "./features/connections/ConnectionsPage";
import { DoctorPage } from "./features/doctor/DoctorPage";
import { PlaygroundPage } from "./features/playground/PlaygroundPage";
import { InspectLayout } from "./features/inspect/InspectLayout";
import { SessionDetailPage } from "./features/session/SessionDetailPage";
import { SessionsListPage } from "./features/session/SessionsListPage";
import { RootLayout } from "./layouts/RootLayout";

const rootRoute = createRootRoute({ component: RootLayout });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/dashboard" });
  },
});

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/dashboard",
  component: DashboardPage,
});

const chatRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/chat",
  component: ChatPage,
});

// Terminal & Files: top-level redirects to the matching Inspect tab.
// Until they get their own pages, the activity-bar entries should at
// least land users on the existing implementation, not a stub.
const terminalRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/terminal",
  beforeLoad: () => {
    throw redirect({
      to: "/inspect/$tabKey",
      params: { tabKey: "shell" },
      replace: true,
    });
  },
});

// Inspect is a nested-route subtree: `/inspect/<tabKey>` per tab. The
// 12 child routes are built programmatically from `INSPECT_TAB_KEYS`
// (the source of truth) and an explicit `InspectTabKey → component`
// map. This avoids the 12-branch conditional in InspectPage and lets
// TanStack Router's `defaultPreload: intent` warm each tab on hover.
//
// Back-compat: bookmarks of the form `/inspect?tab=logcat` land on the
// `inspectIndexRoute`, which redirects to `/inspect/logcat`. Clean
// `/inspect` redirects to `/inspect/system`. SPA fallback test in
// `tests/api/test_ui_static.py` still passes since FastAPI just
// serves index.html for the prefix.
export type InspectTabKey =
  | "system"
  | "charts"
  | "uart"
  | "logcat"
  | "shell"
  | "screenshot"
  | "ui-dump"
  | "files"
  | "power"
  | "log-search"
  | "diag"
  | "app";
export const INSPECT_TAB_KEYS: InspectTabKey[] = [
  "system",
  "charts",
  "uart",
  "logcat",
  "shell",
  "screenshot",
  "ui-dump",
  "files",
  "power",
  "log-search",
  "diag",
  "app",
];

// Lazy-loaded tab components — each tab chunk only fetches on demand,
// matching the previous InspectPage `lazy(...)` setup so bundle splits
// don't regress.
const InspectTabComponents: Record<
  InspectTabKey,
  React.LazyExoticComponent<React.ComponentType>
> = {
  system: lazy(() =>
    import("./features/inspect/SystemInfoTab").then((m) => ({
      default: m.SystemInfoTab,
    })),
  ),
  charts: lazy(() =>
    import("./features/inspect/ChartsTab").then((m) => ({
      default: m.ChartsTab,
    })),
  ),
  uart: lazy(() =>
    import("./features/inspect/UartTab").then((m) => ({ default: m.UartTab })),
  ),
  logcat: lazy(() =>
    import("./features/inspect/LogcatTab").then((m) => ({
      default: m.LogcatTab,
    })),
  ),
  shell: lazy(() =>
    import("./features/inspect/ShellTab").then((m) => ({
      default: m.ShellTab,
    })),
  ),
  screenshot: lazy(() =>
    import("./features/inspect/ScreenshotTab").then((m) => ({
      default: m.ScreenshotTab,
    })),
  ),
  "ui-dump": lazy(() =>
    import("./features/inspect/UiDumpTab").then((m) => ({
      default: m.UiDumpTab,
    })),
  ),
  files: lazy(() =>
    import("./features/inspect/FilesTab").then((m) => ({
      default: m.FilesTab,
    })),
  ),
  power: lazy(() =>
    import("./features/inspect/PowerTab").then((m) => ({
      default: m.PowerTab,
    })),
  ),
  "log-search": lazy(() =>
    import("./features/inspect/LogSearchTab").then((m) => ({
      default: m.LogSearchTab,
    })),
  ),
  diag: lazy(() =>
    import("./features/inspect/DiagTab").then((m) => ({ default: m.DiagTab })),
  ),
  app: lazy(() =>
    import("./features/inspect/AppTab").then((m) => ({ default: m.AppTab })),
  ),
};

const inspectRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/inspect",
  component: InspectLayout,
});

// `/inspect` (no child) → redirect. Preserve `?tab=logcat` style URLs
// from old bookmarks / Dashboard quick-actions written before this
// refactor.
const inspectIndexRoute = createRoute({
  getParentRoute: () => inspectRoute,
  path: "/",
  validateSearch: (search: Record<string, unknown>): { tab?: InspectTabKey } => {
    const raw = search.tab;
    if (typeof raw === "string" && (INSPECT_TAB_KEYS as string[]).includes(raw)) {
      return { tab: raw as InspectTabKey };
    }
    return {};
  },
  beforeLoad: ({ search }) => {
    const target = search.tab ?? "system";
    throw redirect({
      to: "/inspect/$tabKey",
      params: { tabKey: target },
      replace: true,
    });
  },
});

// Each tab is its own child route. Unknown `$tabKey` redirects to
// `/inspect/system` so bad URLs degrade gracefully.
const inspectTabRoutes = INSPECT_TAB_KEYS.map((key) =>
  createRoute({
    getParentRoute: () => inspectRoute,
    path: key,
    component: InspectTabComponents[key],
  }),
);

const inspectTabFallbackRoute = createRoute({
  getParentRoute: () => inspectRoute,
  path: "$tabKey",
  beforeLoad: () => {
    throw redirect({
      to: "/inspect/$tabKey",
      params: { tabKey: "system" },
      replace: true,
    });
  },
});

const playgroundRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/playground",
  component: PlaygroundPage,
});

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sessions",
  component: SessionsListPage,
});

// /sessions/$sessionId — drill-in chat replay for a single ChatSession.
// Wired from the Dashboard RecentSessions card today; eventually the
// /sessions list route will link here too.
const sessionDetailRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sessions/$sessionId",
  component: SessionDetailPage,
});

const filesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/files",
  beforeLoad: () => {
    throw redirect({
      to: "/inspect/$tabKey",
      params: { tabKey: "files" },
      replace: true,
    });
  },
});

const doctorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/doctor",
  component: DoctorPage,
});

const auditRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/audit",
  component: AuditPage,
});

const connectionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/connections",
  component: ConnectionsPage,
});

// `addChildren` must be chained inline so the route-tree TYPE picks
// up `/inspect/$tabKey` and `/inspect/system|charts|...` paths. A
// separate statement keeps it runtime-correct but the type
// inference loses the children.
const routeTree = rootRoute.addChildren([
  indexRoute,
  dashboardRoute,
  chatRoute,
  terminalRoute,
  inspectRoute.addChildren([
    inspectIndexRoute,
    ...inspectTabRoutes,
    inspectTabFallbackRoute,
  ]),
  playgroundRoute,
  sessionsRoute,
  sessionDetailRoute,
  filesRoute,
  doctorRoute,
  auditRoute,
  connectionsRoute,
]);

// Strip the deployment base (e.g. `/app/` in dev + alb-api mount, or
// `/android-llm-bridge/app/` on GitHub Pages) before matching routes,
// so route definitions stay deployment-agnostic.
const RAW_BASE = import.meta.env.BASE_URL || "/";
const BASEPATH = RAW_BASE === "/" ? "" : RAW_BASE.replace(/\/$/, "");

export const router = createRouter({
  routeTree,
  defaultPreload: "intent",
  basepath: BASEPATH || undefined,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
