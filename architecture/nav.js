/* merobox Architecture — Shared Navigation */
(function () {
  'use strict';

  const REPO = 'https://github.com/calimero-network/merobox';
  const PAGES_BASE = './';

  const NAV = [
    { section: 'Overview' },
    { label: 'Home', href: 'index.html', dot: '#f59e0b' },
    { label: 'System Overview', href: 'system-overview.html', dot: '#3b82f6' },
    { section: 'Components' },
    { label: 'Workflow Engine', href: 'workflow-engine.html', dot: '#10b981' },
    { label: 'Node Management', href: 'node-management.html', dot: '#8b5cf6' },
    { label: 'Remote Nodes', href: 'remote-nodes.html', dot: '#ec4899' },
    { label: 'NEAR Integration', href: 'near-integration.html', dot: '#f97316' },
    { section: 'Reference' },
    { label: 'Workflow YAML', href: 'workflow-yaml.html', dot: '#f97316' },
    { label: 'CLI Reference', href: 'cli-reference.html', dot: '#06b6d4' },
    { label: 'Error Handling', href: 'error-handling.html', dot: '#ef4444' },
    { section: 'Guides' },
    { label: 'Testing', href: 'testing.html', dot: '#84cc16' },
    { label: 'Troubleshooting', href: 'troubleshooting.html', dot: '#f59e0b' },
    { label: 'Glossary', href: 'glossary.html', dot: '#84cc16' },
  ];

  function currentPage() {
    const p = location.pathname;
    for (const item of NAV) {
      if (!item.href) continue;
      if (p.endsWith(item.href) || p.endsWith('/' + item.href)) return item.href;
    }
    if (p.endsWith('/') || p.endsWith('/architecture/') || p.endsWith('/architecture')) return 'index.html';
    return '';
  }

  function buildSidebar() {
    const sb = document.createElement('nav');
    sb.className = 'sidebar';
    sb.id = 'sidebar';

    const cur = currentPage();

    sb.innerHTML = `
      <div class="sidebar-logo">
        <h2>Calimero <em>merobox</em></h2>
        <p>Architecture Reference</p>
      </div>
      <div class="sidebar-search">
        <input type="text" id="nav-search" placeholder="Search pages..." autocomplete="off"/>
      </div>
      <div class="sidebar-nav" id="nav-links"></div>
      <div class="sidebar-footer">
        <a href="${REPO}" target="_blank" rel="noopener">GitHub &rarr;</a>
      </div>
    `;

    const linksEl = sb.querySelector('#nav-links');
    for (const item of NAV) {
      if (item.section) {
        const s = document.createElement('div');
        s.className = 'nav-section';
        s.textContent = item.section;
        linksEl.appendChild(s);
        continue;
      }
      const a = document.createElement('a');
      a.className = 'nav-link' + (item.sub ? ' sub' : '') + (item.href === cur ? ' active' : '');
      a.href = PAGES_BASE + item.href;
      a.innerHTML = `<span class="nav-dot" style="background:${item.dot}"></span>${item.label}`;
      a.dataset.label = item.label.toLowerCase();
      linksEl.appendChild(a);
    }

    document.body.prepend(sb);

    const btn = document.createElement('button');
    btn.className = 'menu-toggle';
    btn.textContent = '\u2630';
    btn.onclick = () => sb.classList.toggle('open');
    document.body.prepend(btn);

    const search = sb.querySelector('#nav-search');
    search.addEventListener('input', () => {
      const q = search.value.toLowerCase();
      linksEl.querySelectorAll('.nav-link').forEach(a => {
        a.style.display = a.dataset.label.includes(q) ? '' : 'none';
      });
      linksEl.querySelectorAll('.nav-section').forEach(s => {
        let hasVisible = false;
        let el = s.nextElementSibling;
        while (el && !el.classList.contains('nav-section')) {
          if (el.style.display !== 'none') hasVisible = true;
          el = el.nextElementSibling;
        }
        s.style.display = hasVisible ? '' : 'none';
      });
    });
  }

  function buildBreadcrumb(items) {
    const bc = document.querySelector('.breadcrumb');
    if (!bc) return;
    bc.innerHTML = items.map((item, i) => {
      if (i === items.length - 1) return `<span>${item.label}</span>`;
      return `<a href="${item.href}">${item.label}</a><span class="sep">/</span>`;
    }).join('');
  }

  function tabSystem() {
    document.querySelectorAll('[data-tabs]').forEach(container => {
      const tabs = container.querySelectorAll('.tab');
      const panels = container.parentElement.querySelectorAll('.panel');
      tabs.forEach(tab => {
        tab.addEventListener('click', () => {
          tabs.forEach(t => t.classList.remove('on'));
          panels.forEach(p => p.classList.remove('on'));
          tab.classList.add('on');
          const target = document.getElementById(tab.dataset.target);
          if (target) target.classList.add('on');
        });
      });
    });
  }

  function ghLink(path, line) {
    const base = REPO + '/blob/master/';
    const url = line ? base + path + '#L' + line : base + path;
    return `<a class="gh-link" href="${url}" target="_blank" rel="noopener">${path}</a>`;
  }

  document.addEventListener('DOMContentLoaded', () => {
    buildSidebar();
    tabSystem();
  });

  window.arch = { ghLink, buildBreadcrumb, REPO, PAGES_BASE };
})();
