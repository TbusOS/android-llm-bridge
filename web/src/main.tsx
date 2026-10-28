import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { AlbApiError } from "./lib/api";
// v2 component styles first; the v3 (Material 3) sheets load after them so
// a class both define (.btn, .pill, .dot …) takes the v3 look.
import "./styles/components.css";
import "./styles/v3/index.css";
import "./styles/v3/legacy.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // alb-api is local — aggressive refetch isn't needed. Each panel
      // picks its own staleTime via useQuery options.
      staleTime: 30_000,
      retry: (failureCount, error) => {
        // Don't retry validation / client errors (4xx). Server / network
        // errors get one retry.
        if (error instanceof AlbApiError && error.status && error.status < 500) {
          return false;
        }
        return failureCount < 1;
      },
    },
  },
});

const root = document.getElementById("root");
if (!root) throw new Error("#root element missing from index.html");

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
