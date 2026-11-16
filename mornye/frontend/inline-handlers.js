// inline-handlers.js — event handlers written in the markup, without eval.
//
// F1 (docs/redesign-plan.md): a Content-Security-Policy that forbids inline
// script also forbids onclick="…" attributes. The markup keeps its handlers
// as data-on<event>="…" instead (data-onclick, data-onchange, …), and this
// file binds them: each code string is parsed once by the small interpreter
// below — no eval, no new Function — and the element gets a real listener,
// the same element and phase an onclick attribute would have used. Markup
// that app.js adds later (innerHTML of rows, cards, dialogs) is bound by a
// MutationObserver before any of its events can fire.
//
// The language is the subset the handlers use: statements separated by ';',
// `if (…) statement`, `return value`, assignment to a name or a member,
// calls, member access (a.b, a[b]), `this`, `event`, string/number/boolean/
// null/undefined literals, array literals, unary `!` and `-`, the comparisons
// `=== !== == != < > <= >=`, `+`, and `&& ||`. Names resolve to globals:
// window properties, then the top-level `const`/`let` app.js registers with
// MornyeHandlers.expose(). `return false` cancels the event's default.

(function () {
  'use strict';

  // ── Tokenizer ────────────────────────────────────────────────────────────
  const PUNCT = ['===', '!==', '==', '!=', '<=', '>=', '&&', '||',
                 '(', ')', '[', ']', ',', '.', ';', '=', '!', '<', '>', '-', '+'];

  function tokenize(src) {
    const out = [];
    let i = 0;
    while (i < src.length) {
      const c = src[i];
      if (/\s/.test(c)) { i++; continue; }
      if (c === "'" || c === '"' || c === '`') {
        let j = i + 1, s = '';
        while (j < src.length && src[j] !== c) {
          if (src[j] === '\\' && j + 1 < src.length) {
            const n = src[j + 1];
            s += n === 'n' ? '\n' : n === 't' ? '\t' : n;
            j += 2;
          } else { s += src[j++]; }
        }
        if (j >= src.length) throw new SyntaxError('unterminated string');
        out.push({ t: 'str', v: s });
        i = j + 1;
        continue;
      }
      const num = /^\d+(\.\d+)?/.exec(src.slice(i));
      if (num) { out.push({ t: 'num', v: Number(num[0]) }); i += num[0].length; continue; }
      const id = /^[A-Za-z_$][\w$]*/.exec(src.slice(i));
      if (id) { out.push({ t: 'id', v: id[0] }); i += id[0].length; continue; }
      const p = PUNCT.find(x => src.startsWith(x, i));
      if (!p) throw new SyntaxError(`unexpected '${c}'`);
      out.push({ t: 'p', v: p });
      i += p.length;
    }
    out.push({ t: 'end' });
    return out;
  }

  // ── Parser → AST ─────────────────────────────────────────────────────────
  function parse(src) {
    const toks = tokenize(src);
    let k = 0;
    const peek = () => toks[k];
    const isP = (v) => toks[k].t === 'p' && toks[k].v === v;
    const eat = (v) => {
      if (!isP(v)) throw new SyntaxError(`expected '${v}'`);
      k++;
    };

    function statement() {
      const t = peek();
      if (t.t === 'id' && t.v === 'if') {
        k++; eat('(');
        const test = expr();
        eat(')');
        return { k: 'if', test, then: statement() };
      }
      if (t.t === 'id' && t.v === 'return') {
        k++;
        const arg = (peek().t === 'end' || isP(';')) ? null : expr();
        return { k: 'return', arg };
      }
      const e = expr();
      if (isP(';')) k++;
      return { k: 'expr', e };
    }
    function expr() {
      const left = or();
      if (isP('=')) {
        k++;
        if (left.k !== 'name' && left.k !== 'member') throw new SyntaxError('bad assignment');
        return { k: 'assign', target: left, value: expr() };
      }
      return left;
    }
    function binary(next, ops) {
      return function () {
        let l = next();
        while (peek().t === 'p' && ops.includes(peek().v)) {
          const op = toks[k++].v;
          l = { k: 'bin', op, l, r: next() };
        }
        return l;
      };
    }
    function unary() {
      if (isP('!')) { k++; return { k: 'not', a: unary() }; }
      if (isP('-')) { k++; return { k: 'neg', a: unary() }; }
      return postfix();
    }
    const add = binary(unary, ['+']);
    const cmp = binary(add, ['===', '!==', '==', '!=', '<', '>', '<=', '>=']);
    const and = binary(cmp, ['&&']);
    const or = binary(and, ['||']);

    function postfix() {
      let e = primary();
      for (;;) {
        if (isP('.')) {
          k++;
          const t = toks[k++];
          if (t.t !== 'id') throw new SyntaxError('expected a property name');
          e = { k: 'member', obj: e, prop: { k: 'lit', v: t.v } };
        } else if (isP('[')) {
          k++;
          const prop = expr();
          eat(']');
          e = { k: 'member', obj: e, prop };
        } else if (isP('(')) {
          k++;
          const args = [];
          while (!isP(')')) {
            args.push(expr());
            if (!isP(')')) eat(',');
          }
          eat(')');
          e = { k: 'call', fn: e, args };
        } else {
          return e;
        }
      }
    }
    function primary() {
      const t = toks[k++];
      if (t.t === 'str' || t.t === 'num') return { k: 'lit', v: t.v };
      if (t.t === 'p' && t.v === '(') { const e = expr(); eat(')'); return e; }
      if (t.t === 'p' && t.v === '[') {
        const items = [];
        while (!isP(']')) { items.push(expr()); if (!isP(']')) eat(','); }
        eat(']');
        return { k: 'array', items };
      }
      if (t.t === 'id') {
        if (t.v === 'true') return { k: 'lit', v: true };
        if (t.v === 'false') return { k: 'lit', v: false };
        if (t.v === 'null') return { k: 'lit', v: null };
        if (t.v === 'undefined') return { k: 'lit', v: undefined };
        if (t.v === 'this') return { k: 'this' };
        if (t.v === 'event') return { k: 'event' };
        return { k: 'name', v: t.v };
      }
      throw new SyntaxError('unexpected token');
    }

    const list = [];
    while (peek().t !== 'end') {
      if (isP(';')) { k++; continue; }
      list.push(statement());
    }
    return list;
  }

  // ── Evaluator ────────────────────────────────────────────────────────────
  const exposed = Object.create(null);

  function lookup(name) {
    if (name in exposed) return exposed[name];
    if (name in window) return window[name];
    throw new ReferenceError(`${name} is not defined`);
  }

  function ev(n, ctx) {
    switch (n.k) {
      case 'lit': return n.v;
      case 'this': return ctx.el;
      case 'event': return ctx.event;
      case 'name': return lookup(n.v);
      case 'array': return n.items.map(x => ev(x, ctx));
      case 'not': return !ev(n.a, ctx);
      case 'neg': return -ev(n.a, ctx);
      case 'member': return ev(n.obj, ctx)[ev(n.prop, ctx)];
      case 'call': {
        let self, fn;
        if (n.fn.k === 'member') {
          self = ev(n.fn.obj, ctx);
          fn = self[ev(n.fn.prop, ctx)];
        } else {
          fn = ev(n.fn, ctx);
        }
        if (typeof fn !== 'function') throw new TypeError('not a function');
        return fn.apply(self, n.args.map(a => ev(a, ctx)));
      }
      case 'assign': {
        const value = ev(n.value, ctx);
        if (n.target.k === 'name') {
          window[n.target.v] = value;
        } else {
          const obj = ev(n.target.obj, ctx);
          const prop = ev(n.target.prop, ctx);
          // `this.onerror = null` (an image fallback that must not loop):
          // drops the bound handler for that event, as it did the attribute.
          if (obj instanceof Element && /^on[a-z]+$/.test(prop) && value == null) {
            unbind(obj, prop.slice(2));
          }
          obj[prop] = value;
        }
        return value;
      }
      case 'bin': {
        if (n.op === '&&') return ev(n.l, ctx) && ev(n.r, ctx);
        if (n.op === '||') return ev(n.l, ctx) || ev(n.r, ctx);
        const l = ev(n.l, ctx), r = ev(n.r, ctx);
        switch (n.op) {
          case '===': return l === r;
          case '!==': return l !== r;
          case '==': return l == r;
          case '!=': return l != r;
          case '<': return l < r;
          case '>': return l > r;
          case '<=': return l <= r;
          case '>=': return l >= r;
          case '+': return l + r;
        }
      }
    }
    throw new Error(`cannot evaluate ${n.k}`);
  }

  /** Runs statements; a `return` comes back as { value }. */
  function run(stmts, ctx) {
    for (const s of stmts) {
      if (s.k === 'expr') {
        ev(s.e, ctx);
      } else if (s.k === 'return') {
        return { value: s.arg ? ev(s.arg, ctx) : undefined };
      } else if (s.k === 'if' && ev(s.test, ctx)) {
        const r = run([s.then], ctx);
        if (r) return r;
      }
    }
    return null;
  }

  const cache = new Map();
  function compile(code) {
    let c = cache.get(code);
    if (!c) {
      try {
        c = { stmts: parse(code) };
      } catch (err) {
        c = { error: err };
        console.error('[inline-handlers] cannot parse', JSON.stringify(code), err);
      }
      cache.set(code, c);
    }
    return c;
  }

  // ── Binding ──────────────────────────────────────────────────────────────
  const EVENTS = ['click', 'change', 'input', 'keydown', 'keyup', 'submit', 'error', 'load',
                  'dragover', 'dragleave', 'drop', 'dragstart', 'dragend', 'focus', 'blur',
                  'mouseenter', 'mouseleave', 'scroll', 'contextmenu', 'dblclick'];
  const SELECTOR = EVENTS.map(e => `[data-on${e}]`).join(',');
  const BOUND = new WeakMap(); // element → { type: listener }

  function unbind(el, type) {
    const map = BOUND.get(el);
    if (map && map[type]) {
      el.removeEventListener(type, map[type]);
      delete map[type];
    }
  }

  function bindElement(el) {
    let map = BOUND.get(el);
    for (const type of EVENTS) {
      const code = el.getAttribute(`data-on${type}`);
      if (code == null) { if (map && map[type]) unbind(el, type); continue; }
      if (map && map[type] && map[type].code === code) continue;
      if (map && map[type]) el.removeEventListener(type, map[type]);
      const compiled = compile(code);
      const listener = function (event) {
        if (compiled.error) return;
        const r = run(compiled.stmts, { el, event });
        if (r && r.value === false) event.preventDefault();
      };
      listener.code = code;
      if (!map) { map = {}; BOUND.set(el, map); }
      map[type] = listener;
      el.addEventListener(type, listener);
    }
  }

  function bindTree(root) {
    if (!root || root.nodeType !== 1) return;
    if (root.matches(SELECTOR)) bindElement(root);
    root.querySelectorAll(SELECTOR).forEach(bindElement);
  }

  window.MornyeHandlers = {
    /** Registers top-level bindings that are not window properties
     *  (`const $ = …` in app.js) so handlers can name them. */
    expose(bindings) { Object.assign(exposed, bindings); },
    bind: bindTree,
    /** Parses a handler without running it; throws on a syntax error. */
    check(code) { const c = compile(code); if (c.error) throw c.error; },
    /** Runs a handler string as a bound listener would (tests, debugging). */
    run(code, el, event) {
      const c = compile(code);
      if (c.error) throw c.error;
      return run(c.stmts, { el, event });
    },
  };

  bindTree(document.documentElement);
  new MutationObserver((records) => {
    for (const r of records) {
      if (r.type === 'attributes') bindElement(r.target);
      else r.addedNodes.forEach(bindTree);
    }
  }).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true,
    attributeFilter: EVENTS.map(e => `data-on${e}`),
  });
})();
