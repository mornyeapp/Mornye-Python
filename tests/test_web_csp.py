"""F1 (docs/redesign-plan.md): the --web page runs script only from files.

* index.html has no inline <script> body and no on<event>="" attribute;
  handlers are data-on<event>="" and frontend/inline-handlers.js binds them
  without eval (a handler the interpreter cannot parse would be dead);
* app.js and toast-system.js generate no on<event>="" either;
* the page response carries the Content-Security-Policy and its companions,
  and every response carries nosniff.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from mornye import webapp

FRONT = webapp.FRONTEND_DIR
_ON_ATTR = re.compile(r"\son[a-z]+\s*=\s*[\"']", re.I)


class _InlineScriptParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.bodies = []
        self._script_body = None
        self._is_inline = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._script_body = []
            self._is_inline = not any(name == "src" for name, _ in attrs)

    def handle_data(self, data):
        if self._script_body is not None and self._is_inline:
            self._script_body.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script_body is not None:
            self._finish_script()

    def close(self):
        super().close()
        if self._script_body is not None:
            self._finish_script()

    def _finish_script(self):
        if self._is_inline:
            self.bodies.append("".join(self._script_body))
        self._script_body = None
        self._is_inline = False


def _inline_script_bodies(html):
    parser = _InlineScriptParser()
    parser.feed(html)
    parser.close()
    return parser.bodies


def test_index_has_no_inline_script_or_handler_attribute():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    bodies = _inline_script_bodies(html)
    assert not [b for b in bodies if b.strip()], "inline <script> in index.html"
    assert not _ON_ATTR.search(re.sub(r"<!--.*?-->", "", html, flags=re.S))


@pytest.mark.parametrize("tag", ["script", "SCRIPT", "ScRiPt"])
def test_inline_script_check_matches_case_insensitive_tags_and_spaced_end_tag(tag):
    html = f"<{tag}>alert(1)</{tag} >"
    assert _inline_script_bodies(html) == ["alert(1)"]


def test_inline_script_check_ignores_external_scripts():
    html = '<script src="app.js"></script>'
    assert _inline_script_bodies(html) == []


@pytest.mark.parametrize("name", ["app.js", "toast-system.js"])
def test_generated_markup_has_no_handler_attribute(name):
    lines = (FRONT / name).read_text(encoding="utf-8").splitlines()
    code = [line for line in lines if not line.lstrip().startswith(("//", "*"))]
    hits = [
        line.strip()[:100] for line in code if _ON_ATTR.search(line) and "<" in line
    ]
    assert not hits, hits


def test_page_security_headers():
    with TestClient(webapp.create_app()) as client:
        r = client.get("/")
        js = client.get("/app.js")
    csp = r.headers["content-security-policy"]
    assert (
        "script-src 'self'" in csp
        and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    )
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert js.headers["x-content-type-options"] == "nosniff"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_every_markup_handler_parses():
    html = (FRONT / "index.html").read_text(encoding="utf-8")
    codes = [
        c.replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
        for _, c in re.findall(r"\sdata-on[a-z]+=([\"'])(.*?)\1", html)
    ]
    script = (
        "global.window=global;global.document={documentElement:{nodeType:0}};"
        "global.MutationObserver=class{observe(){}};global.Element=class{};"
        f"require({json.dumps(str(FRONT / 'inline-handlers.js'))});"
        "const bad=[];for(const c of JSON.parse(require('fs').readFileSync(0,'utf8')))"
        "{try{MornyeHandlers.check(c)}catch(e){bad.push(c)}}"
        "process.stdout.write(JSON.stringify(bad));"
    )
    out = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(codes),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == []
    assert len(codes) > 150


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_interpreter_semantics():
    script = (
        "global.window=global;global.document={documentElement:{nodeType:0}};"
        "global.MutationObserver=class{observe(){}};global.Element=class{};"
        f"require({json.dumps(str(FRONT / 'inline-handlers.js'))});"
        "const log=[];global.f=(...a)=>log.push(a);"
        "MornyeHandlers.expose({$: (id) => ({id, value: 'v-' + id})});"
        "const el={value:'x',checked:true,dataset:{key:'k'},closest:()=>({dataset:{url:'u'}})};"
        "const evt={key:'Enter',stopPropagation(){log.push('stop')}};"
        "MornyeHandlers.run(\"f('a', 1, this.value, this.checked, [this.dataset.key])\", el, evt);"
        "MornyeHandlers.run(\"event.stopPropagation(); f(this.closest('.r').dataset.url)\", el, evt);"
        "MornyeHandlers.run(\"if(event.key==='Enter' && $('q').value === 'v-q') f('enter')\", el, evt);"
        "MornyeHandlers.run(\"if(event.key==='Escape') f('no')\", el, evt);"
        "const r=MornyeHandlers.run('f(); return false;', el, evt);"
        "MornyeHandlers.run(\"this.src='a' + 'b'\", el, evt);"
        "process.stdout.write(JSON.stringify({log, r, src: el.src}));"
    )
    out = json.loads(
        subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, check=True
        ).stdout
    )
    assert out["log"] == [["a", 1, "x", True, ["k"]], "stop", ["u"], ["enter"], []]
    assert out["r"] == {"value": False}
    assert out["src"] == "ab"
