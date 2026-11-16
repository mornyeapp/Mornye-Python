import test from 'node:test';
import assert from 'node:assert/strict';
import { handlers } from './load.mjs';

// Values built inside the vm context have that realm's prototypes.
const plain = (v) => JSON.parse(JSON.stringify(v));

test('calls, members, this, event and literals', () => {
  const ctx = handlers();
  const log = [];
  ctx.f = (...a) => log.push(a);
  const el = { value: 'v', checked: true, dataset: { key: 'k' } };
  ctx.MornyeHandlers.run("f('a', 1, -2, true, null, this.value, this.checked, [this.dataset.key])", el, {});
  assert.deepEqual(plain(log), [['a', 1, -2, true, null, 'v', true, ['k']]]);
});

test('if with && and ===, and names registered with expose()', () => {
  const ctx = handlers();
  const log = [];
  ctx.f = (x) => log.push(x);
  ctx.MornyeHandlers.expose({ $: (id) => ({ value: id === 'q' ? 'hit' : '' }) });
  ctx.MornyeHandlers.run("if(event.key==='Enter' && $('q').value === 'hit') f('yes')", {}, { key: 'Enter' });
  ctx.MornyeHandlers.run("if(event.key==='Enter') f('no')", {}, { key: 'Escape' });
  assert.deepEqual(log, ['yes']);
});

test('return false comes back as the value; assignment to members works', () => {
  const ctx = handlers();
  ctx.f = () => {};
  assert.deepEqual(plain(ctx.MornyeHandlers.run('f(); return false;', {}, {})), { value: false });
  const el = {};
  ctx.MornyeHandlers.run("this.src = 'a' + 'b'", el, {});
  assert.equal(el.src, 'ab');
});

test('unknown names and syntax errors are errors, never silent', () => {
  const ctx = handlers();
  assert.throws(() => ctx.MornyeHandlers.run('nope()', {}, {}), /not defined/);
  assert.throws(() => ctx.MornyeHandlers.check("f('unterminated"));
  assert.throws(() => ctx.MornyeHandlers.check('f(1;'));
});

test('no eval: the interpreter source never calls eval or Function', async () => {
  const { source } = await import('./load.mjs');
  const code = source('inline-handlers.js').split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
  assert.doesNotMatch(code, /\beval\s*\(|new Function|\bFunction\s*\(/);
});
