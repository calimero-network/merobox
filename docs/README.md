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
- **Workflows** — the cloud-v2 YAML format, the step reference, and the examples.
- **Guides** — porting v1 workflows, and testing with merobox.
- **Reference** — the CLI and troubleshooting.
