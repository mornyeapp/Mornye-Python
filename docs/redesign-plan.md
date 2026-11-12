# Piano: nuovo look per GUI e TUI, da zero

Documento di lavoro. Numeri verificati sul branch `2.0.6` (commit `8c6b323`).

**Obiettivo:** sostituire per intero il linguaggio visivo di Mornye — GUI
(`mornye/frontend/`) e TUI (`mornye/tui/`) — con un unico design system nuovo,
senza toccare motore di download, API e flussi.

**Non è in scopo:** riscrittura di `app.js` in un framework, introduzione di
un build step, cambi al contratto delle API di `webapp.py`.

La §7 aggiunge, dopo una scansione della codebase, fix, miglioramenti e
nuove funzioni: sono binari separati dal look, ognuno agganciato alla fase
in cui costa meno farlo.

---

## 1. Da dove si parte

| Pezzo | File | Righe | Nota |
| --- | --- | --- | --- |
| Stile GUI | `frontend/styles.css` | 4905 | 45 `@media`/`@container`, 27 `!important`, strati di rebrand successivi |
| Markup GUI | `frontend/index.html` | 1871 | 10 viste, 215 `style=""` inline, 166 `onclick=` |
| Logica GUI | `frontend/app.js` | 9761 | ~580 punti che toccano classi/stili/`innerHTML` |
| Stile TUI | `tui/mornye.tcss` | 723 | grammatica MovieBox-Tui |
| Vocabolario TUI | `tui/branding.py` | 390 | wordmark, titoli pannello, badge, hint |
| Shell TUI | `tui/app.py` | 980 | 12 pannelli, 8+ temi |
| Token condivisi | `branding_tokens.py` | 131 | solo colori; `tests/test_brand_tokens.py` li tiene allineati al CSS |

Look attuale: "warm dark music app" — neri caldi, teal del logo, ambra,
chrome traslucido, Fraunces + Inter + JetBrains Mono. TUI: wordmark a blocchi,
card arrotondate, badge pieni.

Cosa rende costoso un restyling oggi, e va quindi sistemato **prima** di
disegnare:

1. **Lo stile è in tre posti.** CSS, attributi `style=""` nell'HTML e
   stringhe HTML generate da `app.js`. Un foglio nuovo non li raggiunge tutti.
2. **I token coprono solo il colore.** Spaziature, raggi, scala tipografica,
   ombre e durate sono numeri sparsi: cambiarli significa cercarli uno a uno.
3. **Nessuna rete visiva.** I test verificano il cablaggio
   (`test_frontend_new_ui_wiring.py`) e il vocabolario TUI (`test_tui_look.py`),
   non l'aspetto. Una regressione di layout passa inosservata.

---

## 2. Direzione visiva — **scelta: A · Console da studio** (2026-11-12)

Mornye come un apparecchio hi-fi da rack: grafite, un solo colore segnale,
dati tecnici letti su un display. Densa, piatta, precisa. Scelta perché si
traduce quasi 1:1 in un terminale: GUI e TUI diventano la stessa interfaccia
in due materiali. (Scartate: "Copertina di disco", chiara e guidata
dall'artwork; "Nativo morbido", troppo vicina all'attuale.)

### Regole comuni

| Aspetto | Regola |
| --- | --- |
| Neutri | Grafite senza dominante (né calda né blu), 4 livelli di superficie |
| Colore | **Un solo segnale** per focus, selezione, azione primaria, livello attivo. Rosso/verde solo come stato, mai come decorazione |
| Separazione | Filetti da 1px. Niente card in rilievo, ombre, blur, gradienti d'ambiente |
| Forma | Raggio 2–3px. Griglia a 4px, righe da 32px |
| Testo | Inter per l'interfaccia; JetBrains Mono, cifre tabulari, per **ogni** numero e dato tecnico. Fraunces esce |
| Etichette | Sezioni in maiuscoletto spaziato, come le serigrafie di un pannello |
| Movimento | ≤120ms, solo cambi di stato. Niente animazioni d'ingresso |

Tre elementi firma, identici nei due mondi:

1. **Il display di qualità** — `24 / 96 · FLAC` in mono sul colore segnale,
   al posto dei badge colorati (Hi-Res, Atmos, Lossless).
2. **Il misuratore a segmenti** — l'avanzamento è una barra a tacche tipo
   LED (`▮▮▮▮▯▯▯▯`), non un riempimento liscio.
3. **L'etichetta di pannello** — il titolo sta *nel* filetto del bordo.

### Solo GUI
Rail di navigazione stretto con icone + etichetta, titlebar e dock opachi.
L'artwork resta l'unica immagine a colori: quadrata, senza raggio, senza
alone.

### Solo TUI
Via il wordmark a blocchi: una riga d'intestazione `MORNYE 2.0.6 ── stato`.
Bordi a linea singola squadrati (non arrotondati), badge come letture tra
parentesi nel colore segnale invece che a fondo pieno, hint in basso
invariati nella forma `[key] action`.

### Default presi, da confermare
- **Segnale: ambra** (tipo display al fosforo, ~`#ffb000`), non il teal
  attuale — altrimenti il cambio non si percepisce. Valori definitivi alla
  voce 8, sotto test di contrasto.
- **Logo:** resta com'è fino alla Fase 5; lì si valuta una versione mono.
- **Tema chiaro:** derivato ("carta"), disegnato dopo lo scuro ma coperto
  dagli stessi test.
- **Accenti selezionabili (`ACCENTS`):** restano, ma cambiano solo il
  segnale. **Temi TUI di terze parti:** restano, sono gratis in Textual.

---

## 3. Architettura del nuovo stile

**Token v2 — `branding_tokens.py` resta l'unica fonte.** Si estende oltre il
colore: `space-*`, `radius-*`, scala tipografica, `elevation-*`, `motion-*`.
Due livelli: primitivi (`grey-900`) e semantici (`surface`, `text-muted`,
`signal`). GUI e TUI consumano solo i semantici. Il test di parità
Python ↔ CSS e quello di contrasto WCAG AA restano e si allargano ai token
nuovi.

**GUI — CSS a strati, sempre senza build.** `styles.css` viene sostituito da
file piccoli caricati in ordine con `@layer`:

```
frontend/css/tokens.css      ← specchio di branding_tokens.py
frontend/css/base.css        ← reset, tipografia, focus, scrollbar
frontend/css/layout.css      ← shell: rail, titlebar, dock, player bar
frontend/css/components.css  ← bottoni, input, chip, tabelle, modali, toast
frontend/css/views/*.css     ← una per vista
```

Vincolo: gli `id` e i nomi di funzione che `app.js` usa sono un **contratto**
e non cambiano; cambiano classi, struttura della shell e tutto il CSS.

**TUI — stessa regola.** `mornye.tcss` riscritto da zero sopra i token;
`branding.py` ridisegna i quattro elementi d'identità (wordmark, titolo
pannello, badge, hint). Restano intatti i comportamenti: fallback ASCII,
`NO_COLOR`, layout compatto 80×24, log affiancato/impilato.

---

## 4. Fasi

Ogni voce numerata è un commit a sé.

### Fase 0 · Rete di sicurezza (nessun cambio visibile)
1. Script che cattura le 10 viste GUI × scuro/chiaro × 3 larghezze
   (finestra desktop, 768, 375) → baseline in `tests/visual/`.
2. Snapshot SVG dei 12 pannelli TUI a 80×24 e 120×40 (`tui_harness.py` +
   `App.export_screenshot`).
3. Inventario del contratto: `id`/classi che `app.js` legge o scrive, salvato
   come test (fallisce se l'HTML ne perde uno).

### Fase 1 · Bonifica (look invariato, verificato con la Fase 0)
4. I 215 `style=""` di `index.html` diventano classi.
5. Gli stili scritti da `app.js` (`el.style.*`, HTML in stringa con colori)
   diventano classi/attributi di stato (`data-state="failed"`).
6. Colori fuori token in `styles.css` ricondotti ai token.

### Fase 2 · Direzione e design system
7. Mock statici di 3 schermate chiave (Home con album aperto, Coda, pannello
   Download della TUI) nella direzione scelta → approvazione.
8. Token v2 in `branding_tokens.py` + `tokens.css` + temi Textual + test.
9. `frontend/styleguide.html` (solo sviluppo): tutti i componenti in tutti gli
   stati, scuro e chiaro. È il banco di prova prima di toccare le viste.

### Fase 3 · GUI
10. Shell: rail di navigazione, titlebar, dock download, player bar.
11. Home / ricerca / album / tabella tracce (la vista più usata).
12. Coda + pannello falliti · 13. History · 14. Settings (670 righe di
    markup: va riorganizzata, non solo ridipinta) · 15. Local · 16. Following
    · 17. Explore · 18. Stats · 19. Player e Now Playing · 20. Logs.
21. Toast, modali, stati vuoti, skeleton, `prefers-reduced-motion`.
22. Rimozione di `styles.css` e di ogni residuo; aggiornamento `sw.js`,
    `manifest.json`, `MANIFEST.in`/package-data.

### Fase 4 · TUI
23. `branding.py`: nuovo wordmark, titoli, badge, hint (+ fallback ASCII).
24. `mornye.tcss` nuovo: frame, sidebar, form, tabelle, barre, modali.
25. Passata sui 12 pannelli a 80×24, 120×40 e terminale "plain".
26. Elenco temi rivisto; `test_tui_look.py` riscritto dove descrive il
    vecchio vocabolario, lasciato dove descrive capacità.

### Fase 5 · Contorno
27. `desktop_branding.py`, icone, `theme_color`, eventuale nuovo logo.
28. Migrazione accento/tema per chi aggiorna (già esiste `migrateAccent`).
29. `docs/site.css`, screenshot di README e guide, `CHANGELOG.md`.

### Fase 6 · Verifica
30. Confronto visivo completo, contrasto AA, navigazione da tastiera e focus.
31. pywebview su macOS (WKWebView), Windows (WebView2), Linux (WebKitGTK);
    `--web` in Docker; PWA su telefono.

---

## 5. Rischi

- **HTML generato da `app.js`.** È dove un foglio nuovo lascia buchi. Le
  voci 3 e 5 esistono per questo; saltarle sposta il costo nella Fase 3.
- **WebKitGTK datato.** `@layer` e le container query (già in uso) non ci
  sono nelle versioni vecchie: va fissata la versione minima supportata su
  Linux prima della Fase 3.
- **Test che leggono il CSS.** `test_brand_tokens.py` analizza `styles.css`:
  va aggiornato nello stesso commit che sposta i file, e vanno cercati altri
  test che citano quel percorso.
- **Transizione.** Le Fasi 0–1 possono uscire in una 2.0.x; dalla 2 in poi
  conviene un branch dedicato e un rilascio unico: una GUI metà vecchia e
  metà nuova è peggio di entrambe.

## 6. Dove sta il lavoro

Fasi 0–1: piccole ma noiose. Fase 2: breve, è dove si decide tutto. Fase 3:
il grosso (Settings e Home da sole circa metà). Fase 4: circa un quarto
della 3. Fasi 5–6: rifinitura.

---

## 7. Oltre il look: fix, miglioramenti, nuove funzioni

Da una scansione statica del 2026-11-12: `ruff` con regole allargate
(B, S, ASYNC, PERF, SIM, UP, BLE, PLW, RUF, C90), `mypy`, `black --check`,
ricerche mirate su frontend e TUI. **Non** ho avviato l'app né eseguito la
suite (2547 test raccolti): le voci sotto sono ciò che il codice mostra, non
bug riprodotti. `ruff` e `black` con la configurazione attuale sono puliti;
nessun `TODO`/`FIXME` nel codice.

### 7.1 Fix

| # | Cosa | Dove | Quando |
| --- | --- | --- | --- |
| F1 | **Nessuna Content-Security-Policy né header di sicurezza** su `--web` (nessuna occorrenza in `mornye/`). Oggi una CSP è impossibile: 166 `onclick=` inline, script inline, `document.write` per il cache-busting | `webapp.py`, `index.html` | Fase 1: delega eventi con `data-action`, versione degli asset dal backend; poi CSP + `nosniff` + `frame-ancestors` |
| F2 | **3 task asyncio lanciati senza tenerne il riferimento**: possono essere raccolti dal GC a metà e le loro eccezioni spariscono | `core/base.py:75`, `core/progress.py:1115`, `extensions/provider.py:394` | Subito, indipendente |
| F3 | **I/O su disco bloccante dentro funzioni async** (33 punti): su un NAS lento ferma l'event loop, e con lui il WebSocket dei progressi | `webapp.py:1094-1169` (9), `download_engine.py` (5), `core/animated_artwork.py` (5 + `open()` a :198), `extensions/provider.py:1360` | Subito, indipendente; `webapp.py` per primo |
| F4 | **10 errori mypy rimasti** (il commento in `pyproject.toml` ne cita ~177). I due che contano: tipo di ritorno sbagliato a `extensions/provider.py:700`, `None` passato come `str` a `extensions/runtime.py:463` | 7 file | Subito; poi mypy diventa bloccante in CI |
| F5 | XML remoto letto con `ElementTree` della stdlib (risposte WebDAV, TTML dei testi) | `core/webdav.py:155`, `core/apple_ttml.py:227` | **Fatto** — `core/safe_xml.py` rifiuta DTD ed entità, senza nuova dipendenza |
| F6 | **530 `except Exception`, 70 dei quali con solo `pass`**. `app.py` ne ha 58: è l'API della GUI, quindi errori che dovrebbero diventare un toast spariscono | `app.py` per primo | Fase 3, vista per vista: almeno un log a debug, e un errore visibile dove l'utente aspetta un esito |
| F7 | **Accessibilità GUI**: 19 `div`/`span` cliccabili non raggiungibili da tastiera, 8 `outline: none`, 6 `<img>` senza `alt`, 11 `confirm()` nativi (in pywebview escono fuori stile) | `index.html`, `app.js`, `styles.css` | Fasi 2–3: nascono corretti nei componenti nuovi (bottone, modale di conferma) |
| F8 | **Strumenti disallineati**: pre-commit fissa `black` 24.0 ma `dev` richiede ≥26.10.1; la CI prova solo 3.11/3.12 mentre i classifier dichiarano 3.13 e 3.14; nessun job macOS per un'app desktop | `.pre-commit-config.yaml`, `ci.yml` | Subito |
| F9 | Il tema scelto con `t` nella TUI non risulta salvato da nessuna parte: da verificare a mano, e nel caso persisterlo | `tui/app.py:792` | Fase 4, voce 26 |

Controllato e **non** un problema: i 10 `innerHTML` con interpolazione non
passata da `escHtml` contengono solo numeri o testo fisso; i 5 avvisi B023
in `network_destinations.py`/`webdav.py` sono lambda eseguite subito.

### 7.2 Miglioramenti

| # | Cosa | Quando |
| --- | --- | --- |
| M1 | **`app.js` in moduli ES per vista** (oggi 9761 righe, 396 funzioni, 123 variabili globali). `<script type="module">`, sempre senza build. Da verificare che pywebview li carichi su tutti e tre i backend | Fase 3, insieme alla vista che si ridisegna |
| M2 | **Spezzare le tre funzioni più intricate**: `create_app` (complessità 92), `amain` nel launcher (73), `build_v1_router` (53) | Indipendente; `create_app` prima di F1 |
| M3 | **Allargare `ruff` a lotti**: prima `RUF006`, `ASYNC`, `B` (bug veri), poi `UP` (40 correzioni automatiche) | Dopo F2/F3, che ne azzerano la maggior parte |
| M4 | **Test per il JavaScript**: oggi c'è solo `node --check`. `node:test` per le funzioni pure, e lo stesso browser pilotato della Fase 0 per un giro di fumo sulle viste | Fase 0, voce 1 |
| M5 | **Soglia di copertura** dal 30% attuale a salire, a scatti | Dopo M4 |
| M6 | **Estrarre le stringhe dell'interfaccia** in un dizionario unico (oggi inglese cablato in HTML e JS) | Fase 1: si toccano già tutte le righe |

### 7.3 Nuove funzioni (proposte, da scegliere)

| # | Cosa | Perché ora |
| --- | --- | --- |
| N1 | **Palette comandi e scorciatoie nella GUI** (Ctrl/Cmd+K). Oggi la GUI risponde solo a `Esc`; la TUI ha già `/`, `?`, `j`/`k`, `Ctrl+R` | È il gesto naturale di una "console", e le stesse azioni servono a F1 (`data-action`) |
| N2 | **"Copia come comando CLI" nella GUI**. La TUI ha il pannello *Command*; nella GUI non esiste | `format_command()` c'è già: è solo esporlo |
| N3 | **Parità TUI**: mancano *Following* (sottoscrizioni), *Local* (tagging dei file locali) ed *Explore*. Gli `api_mixins` li espongono già | Fase 4: i pannelli nuovi nascono nel nuovo stile invece di essere ridipinti dopo |
| N4 | **Densità regolabile** (compatta / comoda) in GUI | Con i token di spaziatura della voce 8 è un attributo, non un lavoro |
| N5 | **Italiano** (e struttura per altre lingue) | Segue M6 a costo quasi nullo |

### 7.4 Ordine consigliato

1. **Prima del redesign, in una 2.0.x:** F2, F3, F4, F5, F8 — piccoli,
   isolati, nessuno tocca l'interfaccia.
2. **Dentro le Fasi 0–1:** M4, F1, M6, M2 (`create_app`).
3. **Dentro le Fasi 2–4:** F7, F6, M1, N1, N2, N4, N3, F9.
4. **Dopo:** M3, M5, N5.
