import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';
import { docLinks } from './src/doc-links.mjs';

const page = (slug) => `docs/${slug}`;

export default defineConfig({
  site: 'https://aishwaryawambule.github.io',
  base: '/digdown',
  markdown: { remarkPlugins: [docLinks] },
  integrations: [
    starlight({
      title: 'digdown',
      description: 'Find out why a Docker container failed, and fix the cause.',
      logo: { light: './src/assets/logo-light.svg', dark: './src/assets/logo-dark.svg' },
      favicon: '/favicon.svg',
      customCss: [
        '@fontsource-variable/schibsted-grotesk',
        '@fontsource/commit-mono/400.css',
        './src/styles/docs.css',
      ],
      social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/aishwaryawambule/digdown' }],
      head: [{
        tag: 'script',
        // Dark first, like the dashboard, unless someone has chosen otherwise.
        content: "if(!localStorage.getItem('starlight-theme'))localStorage.setItem('starlight-theme','dark')",
      }],
      sidebar: [
        { label: 'Start here', items: ['docs', page('try-the-demo'), page('install-with-docker'), page('install-directly'), page('first-time-setup'), page('choose-an-ai'), page('practice-with-the-sandbox')] },
        { label: 'Using Digdown', items: [page('using-the-dashboard'), page('handle-an-incident'), page('auto-heal'), page('choose-apps'), page('alerts')] },
        { label: 'Running it', items: [page('run-on-a-server'), page('watch-another-server'), page('update-and-uninstall'), page('troubleshooting')] },
        { label: 'Reference', items: [page('whats-been-tested'), page('faq'), page('commands'), page('configuration'), page('security'), page('how-it-works')] },
      ],
    }),
  ],
});
