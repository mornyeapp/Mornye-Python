// keynav.js — moving around the sidebar and the Player with the arrow keys.
// Loaded after app.js, whose functions and `lib` it uses.
//
// Up/Down walk the sidebar rows (playlists included); Right goes into the
// Player's music, Left comes back. In the Player the arrows walk the song
// rows and the cover grid (Left/Right in a row of covers, Up/Down between
// rows), loading the next page past the last one shown; Enter or Space opens
// or plays (data-activate), Escape and Backspace go back a page.
//
// The arrows are claimed only while a sidebar row, a song row or a cover has
// the focus (Up/Down also with nothing focused, on the Player). Everywhere
// else, and always in a field, a slider, a dialog or the now playing view,
// they keep doing what they did: seeking with Left/Right, the field's own
// keys, and so on. Listening in the capture phase puts this ahead of the
// playback shortcuts, which never see a key handled here.
(function () {
  'use strict';

  var OVERLAYS = '.modal:not(.hidden), .api-modal:not(.hidden), #np-view:not(.hidden),'
    + ' #folder-browser-modal:not(.hidden), .palette:not(.hidden)';
  var FIELDS = 'input, textarea, select, [contenteditable=""], [contenteditable="true"],'
    + ' [role="slider"], [role="listbox"], [role="menu"], [role="combobox"]';

  var lastSidebar = null;   // the sidebar row Left comes back to
  var lastContent = null;   // the row or cover Right goes back to

  function shown(el) {
    return !!el && el.isConnected && el.getClientRects().length > 0;
  }
  function onPlayer() {
    var v = document.getElementById('view-player');
    return !!v && v.classList.contains('active');
  }
  // On a phone the sidebar is the bar along the bottom: its rows go sideways.
  function sidebarAcross() {
    return document.body.classList.contains('web-mode')
      && window.matchMedia('(max-width: 700px)').matches;
  }

  function show(el) {
    if (!el) return false;
    el.focus({ preventScroll: true });
    el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    return true;
  }

  // ── Sidebar ───────────────────────────────────────────────────────────────
  function sidebarRows() {
    return Array.prototype.filter.call(document.querySelectorAll('#sidebar .nav-btn'), shown);
  }
  // The row of the page on screen; else the one the focus came from.
  function sidebarTarget() {
    var all = sidebarRows();
    return all.find(function (b) { return b.classList.contains('active') && b.closest('#sb-playlist-list'); })
      || all.find(function (b) { return b.classList.contains('active'); })
      || (shown(lastSidebar) && lastSidebar.closest('#sidebar') ? lastSidebar : null)
      || all[0] || null;
  }
  function sidebarKey(e, btn) {
    var across = sidebarAcross();
    var prev = across ? 'ArrowLeft' : 'ArrowUp';
    var next = across ? 'ArrowRight' : 'ArrowDown';
    if (e.key === prev || e.key === next) {
      var all = sidebarRows();
      var i = all.indexOf(btn) + (e.key === next ? 1 : -1);
      if (i >= 0 && i < all.length) show(all[i]);
      return true;
    }
    if (!across && e.key === 'ArrowRight') {
      var target = contentTarget();
      if (!target) return false;
      lastSidebar = btn;
      return show(target);
    }
    return false;
  }

  // ── Player: song rows and the cover grid ──────────────────────────────────
  function cards() {
    var grid = document.getElementById('player-grid');
    return grid && !grid.classList.contains('hidden') ? grid.querySelectorAll('.pl-card') : [];
  }
  function rows() {
    var list = document.getElementById('player-list');
    return list ? list.querySelectorAll('.pl-row') : [];
  }
  function firstItem() {
    var c = cards();
    if (c.length && shown(c[0])) return c[0];
    var r = rows();
    return r.length && shown(r[0]) ? r[0] : null;
  }
  function contentTarget() {
    if (!onPlayer()) return null;
    if (shown(lastContent) && lastContent.closest('#player-grid, #player-list')) return lastContent;
    return firstItem();
  }

  // Covers in one row of the grid: those level with the first one.
  function columns(list) {
    var top = list[0].offsetTop, n = 1;
    while (n < list.length && list[n].offsetTop === top) n++;
    return n;
  }
  // One more page of covers or rows, if the list has one waiting.
  function loadMore(kind) {
    if (kind === 'card' && document.getElementById('player-grid-sentinel') && typeof libAppendCards === 'function') {
      libAppendCards(); return true;
    }
    if (kind === 'row' && document.getElementById('player-sentinel') && typeof libAppendRows === 'function') {
      libAppendRows(); return true;
    }
    return false;
  }

  function cardKey(e, card) {
    var list = Array.prototype.slice.call(cards());
    var i = list.indexOf(card);
    if (i < 0) return false;
    var cols = columns(list), n = list.length;
    switch (e.key) {
      case 'ArrowLeft':
        if (i % cols) return show(list[i - 1]);
        return toSidebar(card);
      case 'ArrowRight':
        if ((i + 1) % cols && i + 1 < n) show(list[i + 1]);
        else if (i + 1 >= n && loadMore('card')) show(cards()[i + 1]);
        return true;
      case 'ArrowUp':
        if (i - cols >= 0) show(list[i - cols]);
        return true;
      case 'ArrowDown':
        if (i + cols < n) return show(list[i + cols]);
        if (loadMore('card')) {
          var more = cards();
          return show(more[Math.min(i + cols, more.length - 1)]);
        }
        // The last row may be short: from above it, go to its last cover.
        if (Math.floor(i / cols) < Math.floor((n - 1) / cols)) show(list[n - 1]);
        return true;
    }
    return false;
  }

  function nextRow(row, step) {
    var next = row[step];
    while (next && !next.classList.contains('pl-row')) next = next[step];
    return next;
  }
  function rowKey(e, row) {
    if (e.key === 'ArrowLeft') return toSidebar(row);
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return false;
    var step = e.key === 'ArrowDown' ? 'nextElementSibling' : 'previousElementSibling';
    var next = nextRow(row, step);
    if (!next && e.key === 'ArrowDown' && loadMore('row')) next = nextRow(row, step);
    if (next) show(next);
    return true;
  }

  function toSidebar(from) {
    var target = sidebarTarget();
    if (!target) return false;
    lastContent = from;
    return show(target);
  }

  // ── Back a page ───────────────────────────────────────────────────────────
  // Back from an album, artist or playlist lands on its cover again.
  function goBack() {
    if (typeof lib === 'undefined' || typeof playerBack !== 'function') return false;
    if (!lib.album && !lib.artist && !lib.playlist) return false;
    var sel = lib.playlist ? '[data-player-playlist="' + CSS.escape(lib.playlist) + '"]'
      : lib.album ? '[data-album="' + CSS.escape(lib.album) + '"]'
      : '[data-artist="' + CSS.escape(lib.artist) + '"]';
    playerBack();
    var card = null;
    for (var guard = 0; guard < 200; guard++) {
      card = document.querySelector('#player-grid .pl-card' + sel);
      if (card || !loadMore('card')) break;
    }
    lastContent = null;
    show(card || firstItem());
    return true;
  }

  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key;
    var arrow = k === 'ArrowUp' || k === 'ArrowDown' || k === 'ArrowLeft' || k === 'ArrowRight';
    var back = k === 'Escape' || k === 'Backspace';
    if (!arrow && !back && k !== 'Enter' && k !== ' ') return;
    var t = e.target instanceof Element ? e.target : null;
    if (t && t.closest(FIELDS)) return;
    if (document.querySelector(OVERLAYS)) return;

    // Opening a cover redraws the page under the focus: carry it to the
    // first row or cover of the page that opens.
    if (k === 'Enter' || k === ' ') {
      if (t && t.matches('#player-grid .pl-card') && !e.repeat) {
        setTimeout(function () {
          var a = document.activeElement;
          if (!a || a === document.body || !a.isConnected) show(firstItem());
        }, 0);
      }
      return;
    }

    var handled = false;
    var onBody = !t || t === document.body || t === document.documentElement;
    if (t && t.matches('#sidebar .nav-btn')) {
      handled = arrow && sidebarKey(e, t);
    } else if (t && onPlayer() && t.matches('#player-grid .pl-card')) {
      handled = arrow ? cardKey(e, t) : goBack();
    } else if (t && onPlayer() && t.matches('#player-list .pl-row')) {
      handled = arrow ? rowKey(e, t) : goBack();
    } else if (onBody && onPlayer()) {
      // Nothing focused: Up/Down step into the music (Left/Right still seek),
      // Escape goes back (Backspace still resets the speed).
      if (k === 'ArrowDown' || k === 'ArrowUp') handled = show(contentTarget());
      else if (k === 'Escape') handled = goBack();
    }
    if (handled) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
})();
