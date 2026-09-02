// Inlines the dist-demo build into one self-contained HTML file suitable for
// static hosting (Claude Artifacts): title + inlined CSS + root + inlined JS.
// Run `vite build --config vite.demo.config.ts` first.
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const distDir = path.join(webRoot, 'dist-demo');
const assetsDir = path.join(distDir, 'assets');

const assets = readdirSync(assetsDir);
const jsFile = assets.find((f) => f.endsWith('.js'));
const cssFile = assets.find((f) => f.endsWith('.css'));
if (!jsFile || !cssFile) throw new Error(`missing bundle in ${assetsDir}: ${assets.join(', ')}`);

let js = readFileSync(path.join(assetsDir, jsFile), 'utf8');
const css = readFileSync(path.join(assetsDir, cssFile), 'utf8');
// A literal "</script>" inside the bundle would terminate the inline tag early.
js = js.replaceAll('</script>', '<\\/script>');

const html = `<title>SurplusNet</title>
<style>
${css}
</style>
<div id="root"></div>
<script type="module">
${js}
</script>
`;

const outDir = path.join(webRoot, 'dist-artifact');
mkdirSync(outDir, { recursive: true });
const outFile = path.join(outDir, 'surplusnet.html');
writeFileSync(outFile, html);
console.log(`wrote ${outFile} (${(html.length / 1024).toFixed(0)} kB)`);
