// The docs live in the repo's docs/ folder and link to each other as `alerts.md`, which
// works when reading them on GitHub. This points those links at the website's pages.
const BASE = '/digdown/docs';

function rewrite(url) {
  const match = /^(?:\.\/)?([\w-]+)\.md(#.*)?$/.exec(url);
  if (!match) return url;
  const [, page, hash = ''] = match;
  return page === 'index' ? `${BASE}/${hash}` : `${BASE}/${page}/${hash}`;
}

function walk(node) {
  if (node.type === 'link' && node.url) node.url = rewrite(node.url);
  for (const child of node.children || []) walk(child);
}

export function docLinks() {
  return (tree) => walk(tree);
}
