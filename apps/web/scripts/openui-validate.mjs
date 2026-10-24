// Validate OpenUI Lang programs against the PodOS library with the real OpenUI parser.
// stdin: JSON array of programs (fence contents). stdout: JSON array of {ok, errors, components}.
import { readFileSync } from "node:fs";
import { createParser } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui";

const spec = JSON.parse(readFileSync(new URL("../lib/openui/spec.json", import.meta.url)));
const keep = new Set(spec.keep);
const lib = openuiLibrary.extend({ components: { remove: Object.keys(openuiLibrary.components).filter((n) => !keep.has(n)) } });
const parser = createParser(lib.toJSONSchema(), lib.root);

const used = (node, out = new Set()) => {
  if (!node || typeof node !== "object") return out;
  if (Array.isArray(node)) { node.forEach((n) => used(n, out)); return out; }
  if (typeof node.typeName === "string") out.add(node.typeName);
  Object.values(node).forEach((v) => used(v, out));
  return out;
};

const programs = JSON.parse(readFileSync(0, "utf8"));
console.log(JSON.stringify(programs.map((p) => {
  const res = parser.parse(p);
  const errors = (res?.meta?.errors ?? []).map((e) => e.code ?? String(e));
  return { ok: Boolean(res?.root) && errors.length === 0, errors, components: [...used(res?.root)].sort() };
})));
