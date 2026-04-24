# alb Web UI

React 19 + Vite + TypeScript source. Built output lands in
`../docs/app/` which ships inside the pip wheel and is also what
GitHub Pages serves.

End users never touch this directory — they run `alb serve` and the
pre-built bundle is ready.

## Local development

```sh
cd web
npm install          # once
npm run dev          # starts Vite on http://localhost:5173
                     # proxies /api, /chat*, /playground*, /metrics, /terminal
                     # to a running alb-api on http://localhost:8765
```

In another terminal:

```sh
alb-api              # or: uvicorn alb.api.server:app
```

Open <http://localhost:5173> — any change hot-reloads.

## Production build

```sh
npm run build        # outputs to ../docs/app/
```

Commit the `docs/app/` changes along with your source edits — CI
verifies the tree is in sync.

## Offline-first rules

- **No external HTTP at runtime.** Fonts, icons, and every third-party
  asset must be vendored.
- **No telemetry.**
- **No external CDN** in `index.html` or imported stylesheets.
- All design tokens come from `docs/assets/anthropic.css` (loaded by
  `index.html`). Don't pull in Tailwind; scoped component CSS is fine.

See `docs/design-decisions.md` ADR-017 for the full rationale.

## Tech stack

- React 19 + Vite 5 + TypeScript (strict)
- TanStack Router / Query / Table / Virtual
- Zustand for cross-module client state
- shadcn/ui pattern (copy-paste components adapted to anthropic.css)
- Radix UI primitives for accessibility
- lucide-react for icons
- xterm.js + µPlot + Monaco (added per feature)
- Biome for lint/format
