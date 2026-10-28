/**
 * Generates /llms.txt — a machine-readable index of the docs for LLM/AI tools
 * (the emerging llms.txt convention). Built from the docs content collection so
 * it never drifts from the pages.
 */
import type { APIRoute } from 'astro';
import { getCollection } from 'astro:content';

const SITE = 'https://calimero-network.github.io';
const BASE = '/merobox';

const TRACKS: Record<string, string> = {
  'get-started': 'Get Started — install, run an example, write a workflow',
  understand: 'Understand — what merobox is, and the glossary',
  workflows: 'Workflows — the cloud-v2 YAML format, step reference and examples',
  guides: 'Guides — porting v1 workflows, testing',
  reference: 'Reference — CLI, troubleshooting',
};

export const GET: APIRoute = async () => {
  const docs = await getCollection('docs');

  const url = (id: string) => {
    const slug = id.replace(/\.(md|mdx)$/, '').replace(/\/index$/, '');
    return `${SITE}${BASE}/${slug}/`.replace(/\/+$/, '/');
  };

  const byTrack: Record<string, typeof docs> = {};
  for (const entry of docs) {
    const track = entry.id.split('/')[0];
    if (!TRACKS[track]) continue;
    (byTrack[track] ??= []).push(entry);
  }

  const lines: string[] = [
    '# merobox',
    '',
    '> A Python CLI that starts a release-v2 (Calimero Cloud) cluster of merod',
    '> nodes, one process per node, and drives it through declarative YAML',
    '> workflows: deploy apps, call them, crash and upgrade nodes, assert on state.',
    '',
    `Docs site: ${SITE}${BASE}/`,
    '',
  ];

  for (const track of Object.keys(TRACKS)) {
    const entries = (byTrack[track] ?? []).sort(
      (a, b) => (a.data.sidebar?.order ?? 0) - (b.data.sidebar?.order ?? 0),
    );
    if (!entries.length) continue;
    lines.push(`## ${TRACKS[track]}`, '');
    for (const e of entries) {
      const desc = e.data.description ? `: ${e.data.description}` : '';
      lines.push(`- [${e.data.title}](${url(e.id)})${desc}`);
    }
    lines.push('');
  }

  return new Response(lines.join('\n'), {
    headers: { 'Content-Type': 'text/plain; charset=utf-8' },
  });
};
