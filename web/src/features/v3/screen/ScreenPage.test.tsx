/**
 * Screen (v3): the tab is in the URL, so `?tab=tree` (and the old
 * /inspect/ui-dump address that redirects there) opens the UI tree.
 */
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useApp } from "../../../stores/app";
import { ScreenPage } from "./ScreenPage";

vi.mock("../../inspect/ScreenshotTab", () => ({ ScreenshotTab: () => <p>screenshot tab body</p> }));
vi.mock("../../inspect/UiDumpTab", () => ({ UiDumpTab: () => <p>ui tree tab body</p> }));

function renderAt(url: string) {
  const root = createRootRoute();
  const tree = root.addChildren([createRoute({ getParentRoute: () => root, path: "/screen", component: ScreenPage })]);
  const router = createRouter({ routeTree: tree, history: createMemoryHistory({ initialEntries: [url] }) });
  render(<RouterProvider router={router} />);
  return router;
}

beforeEach(() => useApp.setState({ lang: "en", device: null }));

describe("Screen page tabs", () => {
  it("?tab=tree opens the UI tree", async () => {
    renderAt("/screen?tab=tree");
    expect(await screen.findByText("ui tree tab body")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /UI tree/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: /Screenshots/ })).toHaveAttribute("aria-selected", "false");
  });

  it("no tab, or an unknown one, opens the screenshots", async () => {
    renderAt("/screen?tab=nope");
    expect(await screen.findByText("screenshot tab body")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Screenshots/ })).toHaveAttribute("aria-selected", "true");
  });

  it("picking a tab writes it into the URL", async () => {
    const router = renderAt("/screen");
    await screen.findByText("screenshot tab body");
    fireEvent.click(screen.getByRole("tab", { name: /UI tree/ }));
    expect(await screen.findByText("ui tree tab body")).toBeInTheDocument();
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "tree" }));
    fireEvent.click(screen.getByRole("tab", { name: /Screenshots/ }));
    await waitFor(() => expect(router.state.location.search).toEqual({ tab: "shot" }));
    expect(await screen.findByText("screenshot tab body")).toBeInTheDocument();
  });
});
