import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { AlbApiError } from "./lib/api";
import "./styles/global.css";

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
