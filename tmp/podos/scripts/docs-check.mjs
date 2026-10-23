#!/usr/bin/env node
/** Validate local documentation links, shared Mermaid styling and actual SVG rendering. */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
let repoRoot = appRoot;
try { repoRoot = execFileSync("git", ["rev-parse", "--show-toplevel"], { cwd: appRoot, encoding: "utf8" }).trim(); } catch {}
const theme = JSON.parse(readFileSync(path.join(appRoot, "docs/mermaid-theme.json"), "utf8"));
const ignored = new Set([".git", "node_modules", ".venv", "var", ".next", ".pytest_cache", ".ruff_cache", "__pycache__"]);
const flags = new Set(process.argv.slice(2));
for (const flag of flags) if (!["--links-only", "--sync-theme"].includes(flag)) throw new Error(`Unknown option: ${flag}`);
function yaml(obj, indent = 0) {
  return Object.entries(obj).map(([key, value]) => `${" ".repeat(indent)}${key}:${value && typeof value === "object" ? `\n${yaml(value, indent + 2)}` : ` ${JSON.stringify(value)}`}`).join("\n");
}
const header = `---\n${yaml({ config: theme.config })}\n---\n`;
const styles = `    %% podos-theme:start\n${Object.entries(theme.classes).map(([name, style]) => `    classDef ${name} ${style}\n`).join("")}    %% podos-theme:end`;
function files(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    if (ignored.has(entry.name) || entry.isSymbolicLink()) return [];
    const full = path.join(dir, entry.name);
    return entry.isDirectory() ? files(full) : entry.name.endsWith(".md") ? [full] : [];
  });
}
const markdown = files(repoRoot).sort();
const errors = [];
const diagrams = [];
const documents = new Map();
for (const file of markdown) {
  let source = readFileSync(file, "utf8");
  if (flags.has("--sync-theme")) {
    source = source.replace(/^```mermaid\n([\s\S]*?)^```\s*$/gm, (_, body) => {
      const diagram = body.replace(/^---\n[\s\S]*?\n---\n/, "").replace(/\n\s*%% podos-theme:start[\s\S]*?%% podos-theme:end\s*$/, "").trimEnd();
      return `\`\`\`mermaid\n${header}${diagram}${/^flowchart\b/m.test(diagram) ? `\n${styles}` : ""}\n\`\`\``;
    });
    if (source !== readFileSync(file, "utf8")) writeFileSync(file, source);
  }
  documents.set(file, source);
}
function plain(source) { return source.replace(/^(`{3,}|~{3,})[^\n]*\n[\s\S]*?^\1\s*$/gm, ""); }
function anchors(source) {
  const ids = new Set();
  const duplicates = new Map();
  for (const match of plain(source).matchAll(/^#{1,6}\s+(.+)$/gm)) {
    const base = match[1].replace(/\s+#+\s*$/, "").replace(/<[^>]+>/g, "").toLowerCase().replace(/[^\p{L}\p{N}_ -]/gu, "").replace(/ /g, "-");
    const n = duplicates.get(base) ?? 0;
    ids.add(n ? `${base}-${n}` : base);
    duplicates.set(base, n + 1);
  }
  for (const match of source.matchAll(/\bid=["']([^"']+)["']/g)) ids.add(match[1]);
  return ids;
}
let linkCount = 0;
for (const [file, source] of documents) {
  const relative = path.relative(repoRoot, file);
  const stripped = plain(source);
  for (const match of stripped.matchAll(/!?\[[^\]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+["'][^\n]*["'])?\)/g)) {
    const target = match[1].replace(/^<|>$/g, "");
    if (/^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(target)) continue;
    linkCount++;
    let raw, fragment;
    try { [raw, fragment] = target.split("#").map(value => decodeURIComponent(value)); }
    catch { errors.push(`${relative}: invalid encoded link ${target}`); continue; }
    const resolved = raw ? path.resolve(path.dirname(file), raw.split("?")[0]) : file;
    if (!existsSync(resolved)) errors.push(`${relative}: missing local target ${target}`);
    else if (fragment && documents.has(resolved) && !anchors(documents.get(resolved)).has(fragment)) errors.push(`${relative}: missing heading ${target}`);
  }
  let index = 0;
  for (const match of source.matchAll(/^```mermaid\n([\s\S]*?)^```\s*$/gm)) {
    const body = match[1]; index++;
    const label = `${relative} diagram ${index}`;
    if (!body.startsWith(header)) errors.push(`${label}: shared frontmatter differs; run sync-theme`);
    if (!/^\s*accTitle: .+/m.test(body) || !/^\s*accDescr: .+/m.test(body)) errors.push(`${label}: missing accessible title or description`);
    if (/^flowchart\b/m.test(body)) {
      if (!body.includes(styles)) errors.push(`${label}: shared semantic styles differ; run sync-theme`);
      for (const cls of body.matchAll(/:::([a-zA-Z]+)/g)) if (!Object.hasOwn(theme.classes, cls[1])) errors.push(`${label}: unknown color class ${cls[1]}`);
    }
    diagrams.push({ document: relative, index, body });
  }
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`Checked ${markdown.length} Markdown files, ${linkCount} local links and ${diagrams.length} themed Mermaid diagrams.`);
if (flags.has("--links-only") || flags.has("--sync-theme")) process.exit(0);
const cli = path.join(appRoot, "docs/node_modules/.bin/mmdc");
if (!existsSync(cli)) throw new Error("Install the pinned renderer with: npm --prefix docs ci");
const output = path.join(appRoot, "var/docs/rendered");
rmSync(output, { recursive: true, force: true });
mkdirSync(output, { recursive: true });
const input = path.join(output, "diagrams.md");
writeFileSync(input, diagrams.map(d => `## ${d.document} — diagram ${d.index}\n\n\`\`\`mermaid\n${d.body}\`\`\`\n`).join("\n"));
writeFileSync(path.join(output, "inventory.json"), JSON.stringify(diagrams.map(({ body, ...d }) => d), null, 2) + "\n");
const args = ["-i", input, "-o", path.join(output, "rendered.md"), "-b", "transparent", "--quiet"];
if (process.env.PODOS_DOCS_BROWSER) {
  const config = path.join(output, "puppeteer.json");
  writeFileSync(config, JSON.stringify({ executablePath: process.env.PODOS_DOCS_BROWSER }));
  args.push("-p", config);
}
execFileSync(cli, args, { cwd: appRoot, stdio: "inherit", timeout: 240000 });
const svg = readdirSync(output).filter(name => name.endsWith(".svg"));
if (svg.length !== diagrams.length) throw new Error(`Expected ${diagrams.length} SVGs, received ${svg.length}`);
for (const name of svg) {
  const rendered = readFileSync(path.join(output, name), "utf8").toLowerCase();
  if (!rendered.includes("<title") || !rendered.includes("<desc")) throw new Error(`Missing accessible SVG metadata: ${name}`);
  if (!["#dbeafe", "#ede9fe", "#ccfbf1"].some(color => rendered.includes(color))) throw new Error(`Shared palette absent from rendered output: ${name}`);
}
console.log(`Rendered and checked ${svg.length} SVGs in var/docs/rendered/.`);
