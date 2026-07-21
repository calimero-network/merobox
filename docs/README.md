# merobox Docs

The merobox documentation site, built with [Astro Starlight](https://starlight.astro.build/)
and published to <https://calimero-network.github.io/merobox/>. Theme, favicon,
and the shared Calimero look are ported from
[calimero-network/core](https://github.com/calimero-network/core/tree/master/docs).

## Run it

```sh
cd docs
npm install
npm run dev      # http://localhost:4321/merobox/
npm run build    # static output in dist/
npm run check    # astro build + internal link check (what CI runs)
```

## Layout

Pages live in `src/content/docs/`, grouped into four tracks:

- **Understand** — what merobox is, the system map, and the glossary.
- **Workflows** — the declarative YAML scenario engine and the full step reference.
- **Guides** — node management, remote nodes, the testing harness, NEAR integration.
- **Reference** — the CLI, troubleshooting, and error handling.

> `docs/superpowers/` holds internal design specs and is unrelated to this site
> (Starlight only builds `src/content/docs/`).
