import test from 'node:test';
import assert from 'node:assert/strict';
import { pick, source } from './load.mjs';

const { clampPage } = pick(['clampPage']);

test('a typed page is kept within the pages there are', () => {
  assert.equal(clampPage('7', 20), 7);
  assert.equal(clampPage(' 12 ', 20), 12);
  assert.equal(clampPage('0', 20), 1);
  assert.equal(clampPage('-3', 20), 1);
  assert.equal(clampPage('99', 20), 20);
  assert.equal(clampPage('4.6', 20), 5);
});

test('something that is not a page is refused', () => {
  assert.equal(clampPage('', 20), null);
  assert.equal(clampPage('abc', 20), null);
  assert.equal(clampPage(null, 20), null);
});

test('the page number is a field that jumps to the page typed', () => {
  const html = source('index.html');
  assert.match(html, /id="page-jump"[^>]*data-onchange="goToPage\(this\.value\)"/);
  assert.match(html, /id="page-total"/);
  const app = source('app.js');
  assert.match(app, /^function previousPage\(\) \{ goToPage\(currentPage - 1\); \}/m);
  assert.match(app, /^function nextPage\(\) \{ goToPage\(currentPage \+ 1\); \}/m);
});
