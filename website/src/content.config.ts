import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

export const collections = {
  docs: defineCollection({
    // One copy of the docs: the same files people read on GitHub.
    loader: glob({
      pattern: '*.md',
      base: '../docs',
      generateId: ({ entry }) => (entry === 'index.md' ? 'docs' : `docs/${entry.replace(/\.md$/, '')}`),
    }),
    schema: docsSchema(),
  }),
};
