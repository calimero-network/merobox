// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// merobox documentation — Astro Starlight with the shared Calimero theme
// (Zinc + #a5ff11 lime), ported from calimero-network/core.
export default defineConfig({
  site: 'https://calimero-network.github.io',
  // GitHub project Pages serve under /<repo>/. Change if a custom domain is used.
  base: '/merobox',
  integrations: [
    starlight({
      title: 'merobox',
      description:
        'A Python CLI for running Calimero nodes in Docker and driving them through declarative YAML workflow scenarios — the end-to-end test harness for Calimero.',
      logo: {
        light: './src/assets/logo-light.svg',
        dark: './src/assets/logo-dark.svg',
        alt: 'merobox',
      },
      favicon: '/favicon.svg',
      customCss: ['./src/styles/theme.css'],
      expressiveCode: {
        themes: ['github-dark', 'github-light'],
        styleOverrides: {
          borderRadius: '0.5rem',
          borderColor: 'var(--sl-color-gray-6)',
          codeBackground: 'var(--sl-color-gray-7)',
          codeFontFamily: 'var(--sl-font-mono)',
          frames: {
            editorTabBarBackground: 'var(--sl-color-gray-6)',
            terminalTitlebarBackground: 'var(--sl-color-gray-6)',
          },
        },
      },
      lastUpdated: true,
      editLink: {
        baseUrl: 'https://github.com/calimero-network/merobox/edit/master/docs/',
      },
      head: [
        { tag: 'meta', attrs: { name: 'theme-color', content: '#09090b' } },
      ],
      social: [
        {
          icon: 'github',
          label: 'GitHub',
          href: 'https://github.com/calimero-network/merobox',
        },
      ],
      // Explicit, grouped navigation: Get Started → Understand → Workflows → Guides → Reference.
      sidebar: [
        { label: 'Home', link: '/' },
        {
          label: 'Get Started',
          items: ['get-started/quickstart', 'get-started/first-workflow'],
        },
        {
          label: 'Understand',
          items: ['understand/system-overview', 'understand/glossary'],
        },
        {
          label: 'Workflows',
          items: ['workflows/engine', 'workflows/yaml', 'workflows/examples'],
        },
        {
          label: 'Guides',
          items: [
            'guides/node-management',
            'guides/remote-nodes',
            'guides/testing',
            'guides/pytest-tutorial',
            'guides/recipes',
            'guides/near-integration',
          ],
        },
        {
          label: 'Reference',
          items: [
            'reference/cli',
            'reference/troubleshooting',
            'reference/error-handling',
          ],
        },
      ],
    }),
  ],
});
