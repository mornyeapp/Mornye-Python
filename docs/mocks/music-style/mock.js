// docs/mocks/music-style/mock.js — made-up data and view switching for the
// music-app style mockup. Every name here is invented; covers are the .gN
// gradients of style.css.

const ALBUMS = [
  { t: 'Coastline Hours', a: 'The Paper Lanterns', g: 'g1', y: 2025, q: '24/96' },
  { t: 'Blue Static', a: 'Mira Okafor', g: 'g8', y: 2025, q: '16/44' },
  { t: 'Sunburnt', a: 'Halvard', g: 'g4', y: 2024, q: '16/44' },
  { t: 'Monochrome Days', a: 'Arlo Vance', g: 'g3', y: 2023, q: '24/96' },
  { t: 'After Hours Transit', a: 'Ostra', g: 'g5', y: 2025, q: '24/44' },
  { t: 'Greenhouse', a: 'Ivy & the Weather', g: 'g6', y: 2022, q: '24/48' },
  { t: 'Candyfloss', a: 'Juno Park', g: 'g7', y: 2025, q: 'AAC' },
  { t: 'Red Room', a: 'Kestrel', g: 'g9', y: 2021, q: '16/44' },
  { t: 'Golden Static', a: 'Sol Marin', g: 'g10', y: 2024, q: '24/96' },
  { t: 'Glacier', a: 'Northbound', g: 'g11', y: 2020, q: '24/192' },
  { t: 'Black Box', a: 'Unit 9', g: 'g12', y: 2019, q: '16/44' },
  { t: 'Sea Glass', a: 'The Paper Lanterns', g: 'g2', y: 2023, q: '24/96' },
];

// [title, artist, album index, time, explicit]
const SONGS = [
  ['Low Tide Radio', 'The Paper Lanterns', 0, '3:42'],
  ['Sunburnt', 'Halvard', 2, '3:18'],
  ['Greenhouse', 'Ivy & the Weather', 5, '4:05'],
  ['Blue Static', 'Mira Okafor', 1, '2:57', 1],
  ['Monochrome Days', 'Arlo Vance', 3, '5:12'],
  ['Candyfloss', 'Juno Park', 6, '2:44'],
  ['Night Shift (feat. Halvard)', 'Ostra', 4, '3:36'],
  ['Red Room', 'Kestrel', 7, '3:03', 1],
  ['Sea Glass', 'The Paper Lanterns', 11, '4:21'],
  ['Golden Hour Again', 'Sol Marin', 8, '3:50'],
  ['Thin Ice', 'Northbound', 9, '6:02'],
  ['Signal Lost', 'Unit 9', 10, '4:12', 1],
  ['Harbour Lights', 'The Paper Lanterns', 0, '3:29'],
  ['Static Bloom', 'Mira Okafor', 1, '3:11'],
  ['Paper Planes', 'Juno Park', 6, '2:58'],
  ['Northern Line', 'Northbound', 9, '4:47'],
];

const ARTISTS = [
  ['The Paper Lanterns', 'g1', '2 albums'], ['Mira Okafor', 'g8', '1 album'], ['Halvard', 'g4', '1 album'],
  ['Arlo Vance', 'g3', '1 album'], ['Ostra', 'g5', '1 album'], ['Ivy & the Weather', 'g6', '1 album'],
  ['Juno Park', 'g7', '1 album'], ['Kestrel', 'g9', '1 album'], ['Sol Marin', 'g10', '1 album'],
  ['Northbound', 'g11', '1 album'], ['Unit 9', 'g12', '1 album'], ['Lumen Choir', 'g2', '3 songs'],
];

const TRACKS = [
  ['Harbour Lights', '3:29'], ['Low Tide Radio', '3:42'], ['Coastline Hours', '4:58'], ['Salt in the Speakers', '3:15'],
  ['Weathervane', '4:02'], ['Lighthouse Keeper (Interlude)', '1:24'], ['Undertow', '3:51', 1], ['Postcards', '3:33'],
  ['Night Ferry', '5:06'], ['Gull Song', '2:47'], ['Ebb', '4:18'], ['Coming Home Slowly', '6:11'],
];

const $ = (id) => document.getElementById(id);
const fill = (id, html) => { const el = $(id); if (el) el.innerHTML = html; };
const esc = (s) => s.replace(/&/g, '&amp;');
const PLAY = '<svg width="14" height="14" viewBox="0 0 24 24" fill="#fff"><path d="M7 4v16l13-8z"/></svg>';
const E = (x) => (x ? '<span class="e">E</span>' : '');

const albumCard = (al, sub) => `
  <div class="album" data-go="album">
    <div class="cover ${al.g}"><div class="ov"><span class="play">${PLAY}</span><span class="dots">•••</span></div></div>
    <div class="n">${esc(al.t)}</div><div class="a">${esc(sub || al.a)}</div>
  </div>`;

const labelCard = (title, kind, g, sub) => `
  <div class="album" data-go="album">
    <div class="cover ${g}"><div class="label"><small>${kind}</small>${esc(title)}</div>
      <div class="ov"><span class="play">${PLAY}</span><span class="dots">•••</span></div></div>
    <div class="n">${esc(title)}</div><div class="a">${esc(sub)}</div>
  </div>`;

const songCell = (s, rank) => `
  <div class="song">${rank ? `<span class="rank">${rank}</span>` : ''}<div class="c ${ALBUMS[s[2]].g}"></div>
    <div class="t"><div class="n">${esc(s[0])}${E(s[4])}</div><div class="a">${esc(s[1])}</div></div>
    <span class="q">${ALBUMS[s[2]].q}</span><button class="more">•••</button></div>`;

const artistCard = ([n, g, sub], sq) => `
  <div class="artist"><div class="av ${g}${sq ? ' sq' : ''}">${n[0]}</div><div class="n">${esc(n)}</div><div class="a">${sub}</div></div>`;

function render() {
  // Home: your own downloads (the feed shelves are drawn by renderFeed)
  fill('home-songs', SONGS.slice(0, 9).map((s) => songCell(s)).join(''));
  fill('home-fetches', ALBUMS.slice(0, 6).map((al) => albumCard(al)).join(''));
  const cats = [['Pop', '#e8587a'], ['Hip-Hop', '#5b6ee1'], ['Rock', '#d9643a'], ['Electronic', '#3fb0a8'],
    ['Jazz', '#c9a227'], ['Classical', '#7d6b5d'], ['R&B', '#8e5bd6'], ['Indie', '#4a9d5b']];
  fill('ex-cats', cats.map(([n, c]) => `<div class="cat" style="background:linear-gradient(135deg, ${c}, color-mix(in srgb, ${c} 55%, #000))"><span>${esc(n)}</span></div>`).join(''));

  // Following
  const ago = ['Today', 'Yesterday', '2 days ago', '3 days ago', '5 days ago', '6 days ago'];
  fill('fo-new', [8, 1, 4, 6, 9, 2].map((i, k) => albumCard(ALBUMS[i], `${ALBUMS[i].a} · ${ago[k]}`)).join(''));
  const followed = ARTISTS.slice(0, 8).map((a, i) => [a[0], a[1], i % 3 ? 'Albums & singles · every 6 h' : 'Everything · every day']);
  followed.splice(2, 0, ['Weekend Drive', 'g10', 'Playlist · every 12 h']);
  fill('fo-list', followed.map((a) => artistCard(a, a[2].startsWith('Playlist'))).join(''));

  // Library
  fill('rec', ALBUMS.map((al) => albumCard(al, `${al.a}`)).join(''));
  fill('al', [...ALBUMS].sort((x, y) => x.t.localeCompare(y.t)).map((al) => albumCard(al)).join(''));
  fill('ar', ARTISTS.map((a) => artistCard(a)).join(''));
  fill('so', '<div class="tr th"><span></span><span>Title</span><span>Artist</span><span>Album</span><span>Format</span><span class="time">Time</span><span></span></div>' +
    [...SONGS, ...SONGS.slice(0, 6)].map((s) => `
      <div class="tr"><div class="c ${ALBUMS[s[2]].g}"></div><span class="ell">${esc(s[0])}${E(s[4])}</span>
        <span class="ell muted">${esc(s[1])}</span><span class="ell muted">${esc(ALBUMS[s[2]].t)}</span>
        <span><span class="q">${ALBUMS[s[2]].q}</span></span><span class="time">${s[3]}</span><button class="more">•••</button></div>`).join(''));
  const pls = [['Weekend Drive', 'g10', '42 songs'], ['Late Night Focus', 'g5', '28 songs'], ['Hi-Res Only', 'g11', '113 songs'],
    ['Running', 'g9', '35 songs'], ['Imported: summer.m3u8', 'g4', '64 songs'], ['Sunday Morning', 'g2', '19 songs']];
  fill('pl', pls.map(([t, g, s]) => labelCard(t, 'Playlist', g, s)).join(''));

  // Album page
  fill('tracks', TRACKS.map(([t, d, ex], i) => `
    <div class="tr"><span class="check${i === 6 ? '' : ' on'}"></span><span class="num">${i + 1}</span>
      <span class="ell">${esc(t)}${E(ex)}${i === 1 ? '<div class="a">The Paper Lanterns, Lumen Choir</div>' : ''}</span>
      <span class="q">24/96</span><span class="time">${d}</span><button class="more">•••</button></div>`).join(''));

  // Queue
  const qrow = (i, title, sub, right, pct, st) => `
    <div class="li q-row"><div class="c ${ALBUMS[i].g}"></div>
      <div class="t"><div class="n">${esc(title)}</div><div class="a">${esc(sub)}</div></div>
      <div>${pct != null ? `<div class="status" style="justify-content:space-between;width:100%"><span>${right}</span><span>${pct}%</span></div><div class="progress"><i style="width:${pct}%"></i></div>` : `<span class="status">${right}</span>`}</div>
      <div class="side">${st}</div><button class="more">✕</button></div>`;
  fill('q-now', [
    qrow(1, 'Blue Static', 'Album · Mira Okafor · 11 tracks', 'Track 4 of 11 · Tidal', 38, '<span class="status"><span class="dot" style="background:var(--accent)"></span>Downloading</span>'),
    qrow(8, 'Golden Static', 'Album · Sol Marin · 9 tracks', 'Track 2 of 9 · Qobuz', 17, '<span class="status"><span class="dot" style="background:var(--accent)"></span>Downloading</span>'),
    qrow(6, 'Candyfloss', 'Track · Juno Park', 'Amazon Music', 81, '<span class="status"><span class="dot" style="background:var(--accent)"></span>Downloading</span>'),
  ].join(''));
  fill('q-wait', [
    qrow(4, 'After Hours Transit', 'Album · Ostra · 10 tracks', 'Waiting', null, 'FLAC 24/44'),
    qrow(9, 'Weekend Drive', 'Playlist · 42 tracks', 'Waiting', null, 'Best available'),
    qrow(3, 'Arlo Vance', 'Artist · 4 albums', 'Waiting', null, 'FLAC 24/96'),
  ].join(''));
  fill('q-done', [
    qrow(0, 'Coastline Hours', 'Album · The Paper Lanterns · 12 tracks', '1.2 GB · 4 min', null, '<span class="status"><span class="dot" style="background:var(--ok)"></span>Done</span>'),
    qrow(2, 'Sunburnt', 'Track · Halvard', '38 MB · 12 s', null, '<span class="status"><span class="dot" style="background:var(--ok)"></span>Done</span>'),
    qrow(7, 'Red Room', 'Track · Kestrel', 'No source had it', null, '<span class="status"><span class="dot" style="background:var(--bad)"></span>Failed</span>'),
  ].join(''));

  // History
  const hrow = (s, size, when) => `
    <div class="li h-row"><div class="c ${ALBUMS[s[2]].g}"></div>
      <div class="t"><div class="n">${esc(s[0])}${E(s[4])}</div><div class="a">${esc(s[1])}</div></div>
      <div class="t"><div class="a">${esc(ALBUMS[s[2]].t)}</div></div>
      <span><span class="q">${ALBUMS[s[2]].q}</span></span><span class="side">${size}</span><span class="side">${when}</span>
      <button class="more" title="Show in folder"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/></svg></button></div>`;
  fill('h-today', [[0, '98 MB', '01:12'], [12, '91 MB', '01:11'], [1, '34 MB', '00:48'], [13, '36 MB', '00:47']].map(([i, z, w]) => hrow(SONGS[i], z, w)).join(''));
  fill('h-yday', [[2, '38 MB', '22:31'], [5, '7 MB', '21:04'], [10, '214 MB', '19:55']].map(([i, z, w]) => hrow(SONGS[i], z, w)).join(''));
  fill('h-week', [[4, '172 MB', 'Mon'], [8, '104 MB', 'Sun'], [9, '96 MB', 'Sun'], [11, '33 MB', 'Sat']].map(([i, z, w]) => hrow(SONGS[i], z, w)).join(''));

  // Local files
  const lrow = (file, old, m, cls, s, on) => `
    <div class="tr"><span class="check${on ? ' on' : ''}"></span>
      <div class="file ell"><div class="ell">${esc(file)}</div><div class="a ell">${esc(old)}</div></div>
      <span class="match ${cls}">${m}</span>
      ${s ? `<div class="c ${ALBUMS[s[2]].g}"></div><div class="new ell"><div class="n ell">${esc(s[0])}</div><div class="a ell">${esc(s[1])} — ${esc(ALBUMS[s[2]].t)}</div></div>` : '<span></span><span class="faint">No match found</span>'}</div>`;
  fill('lo', '<div class="tr th"><span class="check on"></span><span>Current file</span><span>Match</span><span></span><span>New tags</span></div>' + [
    lrow('01 harbour lights.flac', 'Unknown Artist · Track 01', '99%', 'hi', SONGS[12], 1),
    lrow('02 low tide radio.flac', 'paper lanterns · low tide', '98%', 'hi', SONGS[0], 1),
    lrow('sunburnt_final_v2.mp3', 'Halvard · (no album)', '95%', 'hi', SONGS[1], 1),
    lrow('Track 07.m4a', 'No tags', '72%', 'mid', SONGS[6], 0),
    lrow('greenhouse (live).flac', 'Ivy and the Weather · Greenhouse', '88%', 'hi', SONGS[2], 1),
    lrow('voice memo 14.m4a', 'No tags', '—', 'no', null, 0),
    lrow('thin_ice.wav', 'Northbound · Glacier', '97%', 'hi', SONGS[10], 1),
    lrow('candyfloss-remaster.flac', 'Juno Park · Candyfloss (2025)', '64%', 'mid', SONGS[5], 0),
  ].join(''));

  // Stats
  const bar = (label, v, max, g) => `<div class="row">${g ? `<div class="c ${g}"></div>` : ''}<span class="ell">${esc(label)}</span><div class="bar"><i style="width:${(v / max) * 100}%"></i></div><span class="v">${v}</span></div>`;
  fill('st-artists', [['The Paper Lanterns', 74, 'g1'], ['Mira Okafor', 56, 'g8'], ['Northbound', 41, 'g11'], ['Halvard', 33, 'g4'], ['Sol Marin', 29, 'g10'], ['Ostra', 22, 'g5']].map(([n, v, g]) => bar(n, v, 74, g)).join(''));
  fill('st-genres', [['Indie Rock', 300], ['Electronic', 182], ['Jazz', 121], ['Ambient', 96], ['Hip-Hop', 71], ['Classical', 42]].map(([n, v]) => bar(n, v, 300)).join(''));
  fill('st-dec', [['60s', 18], ['70s', 96], ['80s', 140], ['90s', 210], ['00s', 260], ['10s', 330], ['20s', 230]].map(([d, v]) => `<div class="col"><i style="height:${(v / 330) * 100}%"></i>${d}</div>`).join(''));

  // Settings: equalizer
  fill('eq', [[32, 2], [64, 3], [125, 1], [250, 0], [500, -1], ['1k', 0], ['2k', 1], ['4k', 2], ['8k', 3], ['16k', 2]].map(([f, db]) =>
    `<div class="b"><span>${db > 0 ? '+' : ''}${db}</span><div class="track"><span class="knob" style="top:${50 - db * 7}%"></span></div><span>${f}</span></div>`).join(''));
}

// ---------- Home feed ----------
// The recommendation shelves of Home come from one source at a time,
// Apple Music by default; the select beside the title swaps it. Each
// source has its own mix names; the mock rotates the made-up catalogue so
// a switch visibly changes what is shown.
const FEEDS = [
  { name: 'Apple Music', mixes: ['Favorites Mix', 'New Music Mix', 'Chill Mix', 'Get Up Mix', 'Heartbreak Mix', 'Friends Mix'] },
  { name: 'Spotify', mixes: ['Daily Mix 1', 'Daily Mix 2', 'Release Radar', 'Discover Weekly', 'On Repeat', 'Time Capsule'] },
  { name: 'Melon', mixes: ['Melon Chart Mix', 'Ballad Mix', 'Dance Mix', 'Indie Mix', 'K-OST Mix', 'Drive Mix'] },
  { name: 'Bugs!', mixes: ['Bugs Pick', 'Mood Mix', 'Morning Mix', 'Late Night Mix', 'Workout Mix', 'Rainy Day Mix'] },
];
const PANEL = { g1: '#6a2c4a', g2: '#1f4f57', g3: '#3a3a3a', g4: '#8a3a0c', g5: '#2e2266', g6: '#1d4a2a', g7: '#a03a62', g8: '#173d66', g9: '#5a1414', g10: '#7a3a1a', g11: '#14585f', g12: '#1c1c1c' };
const rot = (arr, n) => arr.slice(n % arr.length).concat(arr.slice(0, n % arr.length));

function renderFeed(i) {
  const f = FEEDS[i];
  const al = rot(ALBUMS, i * 3);
  const gs = ['g5', 'g10', 'g1', 'g11', 'g9', 'g6'];
  const picks = [
    { why: 'Made for You', t: f.mixes[0], kind: 'Mix', g: gs[i % 6], d: `${al[0].a}, ${al[1].a}, ${al[2].a} and more` },
    { why: `Because you downloaded ${al[3].t}`, t: al[4].t, kind: 'Album', g: al[4].g, d: `${al[4].a} · ${al[4].y}`, album: 1 },
    { why: 'New Release', t: al[5].t, kind: 'Album', g: al[5].g, d: `${al[5].a} · Out this week`, album: 1 },
    { why: 'Made for You', t: f.mixes[1], kind: 'Mix', g: gs[(i + 2) % 6], d: 'Fresh songs from artists you follow' },
    { why: 'Featured Playlist', t: 'Hi-Res Essentials', kind: 'Playlist', g: 'g11', d: 'Lossless and Hi-Res picks, updated weekly' },
  ];
  fill('home-picks', picks.map((p) => `
    <div class="pick" data-go="album"><div class="why">${esc(p.why)}</div>
      <div class="card-in"><div class="art ${p.g}">${p.album ? '' : `<div class="label"><small>${p.kind}</small>${esc(p.t)}</div>`}</div>
        <div class="desc" style="background:${PANEL[p.g]}"><b>${esc(p.t)}</b>${esc(p.d)}</div></div></div>`).join(''));
  fill('ex-mixes', f.mixes.map((t, k) => labelCard(t, 'Made for you', gs[(k + i) % 6], 'Updated today')).join(''));
  fill('ex-new', al.slice(6, 12).map((a) => albumCard(a)).join(''));
  fill('ex-top', rot(SONGS, i * 4).slice(0, 9).map((s, k) => songCell(s, k + 1)).join(''));
  document.querySelectorAll('.feed-src').forEach((e) => { e.textContent = `from ${f.name}`; });
}

// ---------- behaviour ----------
let last = 'home';
function go(view) {
  const np = $('np');
  if (view === 'nowplaying') { np.classList.add('on'); return; }
  np.classList.remove('on');
  if (!document.querySelector(`.view[data-view="${view}"]`)) view = 'home';
  last = view;
  document.querySelectorAll('.view').forEach((v) => v.classList.toggle('on', v.dataset.view === view));
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('on', a.getAttribute('href') === `#${view}`));
  document.querySelector('main').scrollTop = 0;
}

document.addEventListener('click', (ev) => {
  const t = ev.target.closest('[data-go]');
  if (t) { location.hash = t.dataset.go; return; }
  if (ev.target.closest('.np-close')) { location.hash = last; return; }
  const seg = ev.target.closest('.seg button');
  if (seg) {
    seg.parentElement.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === seg));
    if (seg.dataset.pane) document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('on', p.id === seg.dataset.pane));
  }
  const sw = ev.target.closest('.swatches button');
  if (sw) {
    sw.parentElement.querySelectorAll('button').forEach((b) => b.classList.toggle('on', b === sw));
    document.documentElement.style.setProperty('--accent', sw.dataset.c);
  }
});
$('theme').addEventListener('change', (ev) => document.body.classList.toggle('light', ev.target.value === 'light'));
window.addEventListener('hashchange', () => go(location.hash.slice(1) || 'home'));

$('feed').addEventListener('change', (ev) => renderFeed(Number(ev.target.value)));

render();
renderFeed(0);
go(location.hash.slice(1) || 'home');
