// Loads browser code for node:test (M4, docs/redesign-plan.md).
//
// app.js is one classic script whose top level wires the DOM, so it cannot
// be imported as is. pick() cuts named top-level functions out of its
// source (from `function name(` to the closing brace at column 0) and runs
// them in a fresh vm context with the globals they need; the functions
// under test are the pure ones, so a missing DOM is never in the way.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const FRONT = path.resolve(HERE, '..', '..', 'mornye', 'frontend');

export function source(name) {
  return fs.readFileSync(path.join(FRONT, name), 'utf8').replace(/\r\n/g, '\n');
}

export function pick(names, globals = {}) {
  const app = source('app.js');
  const parts = names.map((name) => {
    const m = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(app);
    if (!m) throw new Error(`app.js has no top-level function ${name}`);
    const end = app.indexOf('\n}\n', m.index);
    return app.slice(m.index, end + 2);
  });
  const ctx = vm.createContext({ URL, window: { location: { href: 'http://localhost/' } }, ...globals });
  vm.runInContext(parts.join('\n') + `\n;({ ${names.join(', ')} })`, ctx);
  return vm.runInContext(`({ ${names.join(', ')} })`, ctx);
}

/** Runs inline-handlers.js against a stand-in document; returns MornyeHandlers. */
export function handlers() {
  const ctx = vm.createContext({
    document: { documentElement: { nodeType: 0 } },
    MutationObserver: class { observe() {} },
    Element: class {},
    console,
  });
  ctx.window = ctx;
  vm.runInContext(source('inline-handlers.js'), ctx);
  return ctx;
}
