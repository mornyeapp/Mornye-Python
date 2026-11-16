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

## 0. Stato e ripartenza (aggiornato 2026-11-16)

**Chi riprende legga questa sezione, poi la §2 (direzione scelta) e la §4
(fasi).** Tutto il lavoro è sul branch `2.0.6`, in commit locali **non
pushati**, uno per voce, senza trailer di co-autore.

### Fatto

| Voce | Commit | Note |
| --- | --- | --- |
| F2 · task asyncio senza riferimento | `65e3683` | `loop_runner.spawn()`; usato in `core/base.py`, `core/progress.py`, `extensions/provider.py` |
| F4 · errori mypy | `65ca900` | 0 errori; il passo mypy in CI ora è **bloccante** |
| F5 · XML remoto | `962a344` | `core/safe_xml.py` rifiuta DTD/entità; nessuna dipendenza nuova |
| F3 · I/O bloccante (parte) | `3e6f280` | `/api/browse-folder` e `/api/media` ora `def` (threadpool); unione dei segmenti in `to_thread` |
| F8 · strumenti | `2c6759a` | CI su 3.11–3.14 + macOS; hook `black` a 26.10.1 |
| `fetch()` sincrono per le estensioni | `c9d2d1d` | vedi §8 |
| Ricerca su sorgenti solo-ricerca | `2842669` | vedi §8 |
| Jellyfin: lacune piccole | `a2c6d4c` | vedi §9 |
| Fase 0 · voce 3: contratto `id`/handler/classi | `6c9011f` | `tests/test_frontend_dom_contract.py` + inventario `tests/frontend_dom_contract.json`; unico orfano noto `hc-summary` |
| Fase 0 · voce 2: snapshot TUI | `6ad44f5` | `tests/visual/capture_tui.py` → 48 SVG in `tests/visual/baseline/tui` (12 pannelli × 80×24/120×40 × scuro/chiaro), HOME temporanea, deterministico; `--compare` per il confronto |
| J1 · rete locale per estensione | `f5abb18` | `extensions/local_network.py`, interruttore in Settings → Extensions, API admin `set_extension_local_network` |
| J2 · testi dalle estensioni | `00b7ac3` | `core/extension_lyrics.py`, provider `ext:<nome>` in GUI/TUI/CLI |
| J3 · home feed in Explore | `33c8b4a` | `core/extension_home_feed.py`; selettore "Feed" in Explore (Spotify + ogni estensione con `capabilities.homeFeed`); ogni item ha un `open_url` apribile dal campo URL |
| J4 · link di sorgenti proprie + flag skip | `641c0b1` | i link che nessun client integrato legge (Jellyfin `…/web/#/details?id=`) vanno all'estensione che li rivendica; le tracce portano `extra_info.source_extension`; `download_one_async` rispetta `skipBuiltInFallback` e `skipMetadataEnrichment` |
| Campi impostazioni estensione troppo stretti | `e26ecc5` | il Server URL di Jellyfin era largo 25px |
| Testi Now Playing che escono a destra | `f69b231` | un pezzo temporizzato con più parole ora è diviso parola per parola (vedi sotto) |
| Fase 0 · voce 1: screenshot GUI | `0ceeb2b` | `tests/visual/capture_gui.py` → 60 PNG in `tests/visual/baseline/gui` (10 viste × desktop/768/375 × scuro/chiaro); Playwright + Chrome installato, extra `visual`; `--compare` tollera il rumore di antialiasing |

| Fase 1 · voce 4: `style=""` di `index.html` → classi | `105738d` | 164 dei 217 stili inline diventano utility `u-*` (una dichiarazione ciascuna, in fondo a `styles.css`, selettore raddoppiato `.u-x.u-x` per battere regole come `.s-row input`). Restano inline le proprietà che `app.js` azzera (`display`, `opacity`, …) e 11 dichiarazioni che una regola con id o attributo altrimenti sovrascriverebbe; `tests/test_frontend_inline_styles.py` impedisce di aggiungerne |
| Fase 1 · voce 5: stili scritti da `app.js` | `e5fcef9` | gli `style=""` nel markup generato diventano le stesse utility; i colori con un significato diventano `data-tone` (`muted`/`ok`/`warn`/`error`) e il bordo di Save `data-dirty`. Restano scritti da JS solo valori calcolati o di comportamento: mostra/nascondi, larghezze delle barre, moto dei testi, copertine |
| Fase 1 · voce 6: colori fuori token | `8447302` | `branding_tokens.FIXED` + `FIXED_CHANNELS`: inchiostro e tinte di Now Playing, codice a barre, glifi; trasparenze come `rgba(var(--media-ink-rgb), .62)`. Test: nessun colore crudo nelle dichiarazioni (maschere escluse) |

**Fase 0 completa.** J1–J4 e il fix dei testi sono stati verificati nel
browser (GUI web su una cartella dati di prova): interruttore J1 acceso e
salvato; `ext:jellyfin` primo e spuntato dopo un ricaricamento; Explore con
Apple Music mostra 15 sezioni e il clic apre l'album (17 tracce); con
Jellyfin senza server compare un'unica scheda d'errore. **Mai provato con un
server Jellyfin vero**: login, ricerca, apertura di un link, download
`ORIGINAL`, testi e feed restano da verificare in quest'ordine.

**Fix dei testi.** Una riga sincronizzata con un solo timestamp
(`[00:51.00]<00:51.00>I don't know how … stay`) diventava un unico `span`:
la copia bianca sovrapposta (`::after`, `white-space: pre`) restava su una
riga e usciva dal bordo, quella grigia sotto andava a capo. Ora
`npSplitTimedPhrases()` divide i pezzi in parole e ripartisce il tempo; un
pezzo senza fine propria dura ~0,32 s a parola, mai oltre la riga dopo.
Per il redesign: il componente "riga di testo" deve nascere con questa
regola (una parola = un elemento).

### Non fatto — da qui si riparte

0. **Fase 3 · voce 10 fatta** (branch `redesign`):
   - `293aa15` — **tutta la GUI sui token v2.** `index.html` carica
     `css/tokens.css` prima di `styles.css`; i nomi v1 di `styles.css`
     diventano alias dei token v2 (blocco "Token v2 bridge"); `--text`,
     `--surface`, `--ease` tolti dai blocchi v1 perché sono già nomi v2.
     Font: JetBrains Mono ovunque; `changeFont()` non scrive più Inter
     inline quando la scelta è quella di default (chi ha scelto un altro
     carattere lo tiene finché la voce 14 toglie il selettore). Il test di
     palette ora verifica il ponte; `branding_tokens.DARK/LIGHT` restano
     solo per la TUI fino alla Fase 4. **Gli accenti `data-accent`
     sovrascrivono ancora `--accent-*`**: chi ne ha scelto uno non di
     default lo vede sulle parti vecchie — voce 28.
   - `351dc22` — **shell.** `css/layout.css` (dopo `styles.css`): titlebar,
     rail da 176px con etichette in maiuscoletto e barra segnale sulla voce
     attiva, contatore coda come lettura, dock con meter a 24 tacche
     (`app.js` arrotonda a tacche intere), player bar piatta. Il dock ora
     sta sopra la player bar invece di finirci sotto (difetto preesistente).
     Il layout telefono (barra in basso, ≤700px) è invariato. Verificato
     in scuro, chiaro, rail compresso e 375px.
   La baseline della Fase 0 (`tests/visual/baseline/`) resta il riferimento
   *prima* del redesign e non viene aggiornata: `--compare` ora segnala le
   viste cambiate, ed è atteso.
   - `d53e04d` — **voce 11, Home.** `css/views/home.css`: testata compatta
     al posto dell'hero, barra link/ricerca in controlli da 32px (44px su
     telefono), Fetch primario solo finché non si apre un album
     (`body:has(#album-card:not(.hidden))`), card album piatta con
     copertina quadrata e colonna dati, righe su filetti con lo stato di
     download come barra di 2px a sinistra e azioni neutre, recenti e
     risultati di ricerca sullo stesso margine di 20px. In fondo a
     `styles.css` c'è uno strato di "rebrand" con selettori più specifici
     (`.search-container .mode-btn`, `#csvBtn:hover`…): va risposto selettore
     per selettore. `css/legacy.css` ridisegna `.act-btn`, `.page-btn` e il
     badge "E" in tutte le viste finché ognuna non è rifatta. Il test dei
     token semantici ora copre tutti i fogli in `frontend/css/`.
     Verificato con un album vero, una ricerca vera (Utilities e Spotify Web
     copiate nella sandbox), in chiaro e a 375px.
     **Note per la voce 21:** l'errore di ricerca compare due volte (riga
     di stato sotto la barra e riquadro), contro la regola §8; il toast
     "fetching metadata…" resta visibile durante la ricerca. **Manca** il
     display di qualità sull'album (`24 / 96 · FLAC`): l'interfaccia non lo
     conosce prima del download; da decidere se mostrarlo dalla qualità
     richiesta o dopo il primo brano.
   - `68723c3` — **voce 12, Coda.** `css/views/queue.css`: pannello
     "Downloading now" piatto, quattro metriche come letture, contatori come
     filtri-lettura, elementi in download su filetti con lo stato come parola
     nel suo tono e meter a 20 tacche scritto da `app.js` (tacche intere); la
     riga sotto un elemento fermo non ripete più lo stato. Falliti in un solo
     pannello con filetto rosso a sinistra. `css/legacy.css` ora copre anche
     le parti condivise con History/Following/Settings (`s-header`,
     `seg-tab`, `tb-field`, `page-row`, `row-btn`, `status-pill`) e risponde
     a `.s-actions .act-btn`. Un solo primario: "Start" della barra e "Retry
     all" dei falliti diventano secondari. Verificato con un job vero e con
     elementi d'esempio in memoria per gli stati in corso/falliti (la
     sandbox non ha provider che scaricano).
     **Note per la voce 21:** aggiungere alla coda mostra due toast con lo
     stesso messaggio.
   - **voce 13, History** (commit sotto): nessun foglio proprio, è fatta
     delle parti condivise di `legacy.css`; aggiunti copertina mancante
     neutra, filetto rosso sulle righe fallite, totali come lettura.
     Verificata con le voci vere di "Fetches" e righe di download d'esempio
     (la sandbox non ne ha), anche a 375px. Lo stato vuoto tratteggiato
     resta per la voce 21.
   - `811faac` — **voce 14, Settings, ridisegnata.** `css/views/settings.css`:
     colonna categorie come il rail (solo ≥701px; sul telefono resta la
     striscia orizzontale), sezioni come pannelli piatti con titolo in
     maiuscoletto, righe su filetti, interruttori quadrati, tutti i campi
     a 32px (regola generica con `:where()` per non pesare sulle eccezioni),
     liste provider con glifi neutri, pulsante e lettura della scheda
     Status. **Un solo carattere:** riga del selettore e anteprima tolte,
     `changeFont()` scarta qualunque font salvato; `#config-font` resta
     nascosto perché `app.js` lo legge. Verificate tutte e sei le schede,
     scuro/chiaro/375px.
     **Non fatto (aperto):** la *riorganizzazione* chiesta dalla voce 14
     (rivedere raggruppamento di sezioni e schede, 25 sezioni / 54 righe)
     — questo commit ridisegna, non riordina. Il form "Network
     destinations" (scheda Download) ha un'impaginazione propria stretta
     (riquadro SMB3) da rivedere lì. Accento: il selettore dice ancora
     "Teal" (voce 28).
   - **voce 15, Fix Local Files** (commit sotto): `css/views/local.css`,
     riquadro di trascinamento a filetto tratteggiato (segnale durante il
     trascinamento), campi da 32px, footer a filetto; "Scan Library"
     diventa secondario. Verificata in scuro/chiaro/375px; i risultati di
     una scansione (righe `track-row.local-row`, già coperte da `home.css`)
     non provati: servono file audio veri.
   - **voce 16, Following** (commit sotto): `css/views/following.css`, campi
     e textarea, sottotitolo del blocco "segui più artisti", righe
     settimanali su filetti; "Follow" diventa secondario. Righe degli
     artisti seguiti e uscite della settimana non provate (servono
     iscrizioni vere).
   - **voce 17, Explore** (commit sotto): `css/views/explore.css`, saluto e
     selettore del feed sulla scala, titoli di sezione in maiuscoletto su
     filetto, griglia di copertine quadrate con contorno segnale al
     passaggio. Copertina mancante o non caricata: riquadro neutro con una
     nota invece del logo verde di Spotify (sbagliato su ogni altro feed).
     Verificata con sezioni d'esempio (il feed di Apple non rispondeva in
     quel momento; quello vero era stato provato in J3), scuro/chiaro/375px.
   - **voce 18, Stats** (commit sotto): `css/views/stats.css`, totali come
     striscia di letture, classifiche come meter a 20 tacche; `statsBars()`
     passa `--v` arrotondato a tacche intere. Verificata con dati d'esempio
     (la sandbox non ha cronologia), scuro/chiaro/375px.
   - **voce 19, Player e Now Playing** (commit sotto):
     `css/views/player.css`; righe del player su filetti con barra segnale
     sul brano in riproduzione, controlli a 32px, "Open" secondario. Now
     Playing resta bianco sulla tinta della copertina ma con forme quadre e
     copertina senza alone. `tokens.css` ora include anche `FIXED` e
     `FIXED_CHANNELS`. Verificato con tre WAV di silenzio nella sandbox.
   - **voce 20, Logs** (commit sotto): `css/views/logs.css`, pannello a
     filetto, legenda in maiuscoletto, pulsanti quadrati, messaggi nel colore
     di stato, niente animazione d'ingresso per riga.
   - **voce 21, feedback** (commit sotto): `css/feedback.css` — toast come
     avvisi, modali come dialoghi a filetto, stati vuoti allineati a
     sinistra senza cornice, stati d'errore con filetto rosso, skeleton
     fermi, `prefers-reduced-motion` che spegne tutto. Doppioni tolti:
     errore di ricerca (riga di stato) e toast ripetuto all'aggiunta in
     coda. **Aperto (F7):** gli 11 `confirm()` nativi non sono ancora
     sostituiti dal dialogo della styleguide; il toast "fetching metadata…"
     che resta durante la ricerca non è stato indagato.
   - **voce 22 — rinviata, da fare in una sessione dedicata.** `styles.css`
     contiene ancora tutta la *struttura* (griglie, posizionamenti,
     responsive, barra telefono); i fogli nuovi ne cambiano solo l'aspetto.
     Toglierlo = riscriverne la struttura nei file nuovi. Metodo proposto,
     vista per vista: (1) spostare le regole strutturali della vista da
     `styles.css` al suo `css/views/*.css` (o a `layout.css` per la shell);
     (2) togliere da `styles.css` le regole ormai sovrascritte; (3) dopo
     ogni passo, confronto stili calcolati a runtime prima/dopo con lo
     strumento della Fase 1 (`runtime.py`, ricostruibile: Playwright, GUI
     web su dati di prova, ogni vista e scheda) → 0 differenze; (4) in
     fondo: togliere il blocco "Token v2 bridge" sostituendo i nomi v1 con
     i v2 nei fogli rimasti, rimuovere `styles.css` da `index.html`,
     `sw.js` (SHELL_FILES), `MANIFEST.in`/package-data, aggiornare
     `test_brand_tokens.py`, `test_frontend_inline_styles.py` (utility
     `u-*` → dove finiscono), i font Inter/Fraunces da `assets/fonts/`.
   - **voce 22 — fatta** (2026-11-13 → 16, sessione dedicata, branch
     `claude/zen-curran-97cf7a` da `redesign`, un commit per passo).
     Strumenti ricostruiti nella cartella temporanea della sessione: un
     modello CSS che conserva gli offset (regole, contesti `@media`,
     specificità) e il confronto a runtime degli stili calcolati —
     Playwright, `mornye --web` su una HOME vuota, 140 scenari: le 10 viste,
     le 6 schede di Settings, la scheda Fetches di History e 18 stati con
     dati sintetici passati alle funzioni di `app.js` (album, album con
     selezione, playlist, artista, brano singolo, ricerca, recenti, coda con
     ogni stato e falliti, staged, cronologia, statistiche, Explore,
     Following, Local, toast, modali, drawer, rail chiusa), ciascuno a
     1280×800 e 375×812, scuro e chiaro. Per ogni elemento (e `::before`,
     `::after`, `::placeholder`, `::marker`) ogni proprietà calcolata,
     custom property comprese, più il riquadro; animazioni ferme a t=0.
     Due esecuzioni senza modifiche: 0 differenze.
     1. **Dichiarazioni morte.** 1134 delle 5055 dichiarazioni di
        `styles.css` sono ridichiarate più avanti con lo stesso identico
        selettore, in un contesto uguale o più largo, con importanza pari o
        maggiore e un valore che ogni motore legge (niente `dvh`, prefissi,
        `text-wrap`): non vincono mai. Tolte, con i commenti che
        descrivevano solo loro. Confronto: 0 differenze.
     2. **Selettori senza markup.** 80 regole (e un selettore di una lista)
        chiedevano una classe che né `index.html`, né gli script, né il
        Python scrivono più: la vecchia scheda dettaglio brano
        (`.track-detail-*`), le righe del vecchio health check (`.hc-*`),
        `.save-toast`, `.queue-toolbar`, `.queue-stats-bar`, `.sort-btn`,
        `.artist-section-hdr`, `.hero-badge`, `.clear-link`/`.export-link` e
        simili (25 nomi, cercati in tutto il repository). Restano le classi
        composte a runtime (`reg-badge-${…}`, le posizioni dei toast).
        Confronto: 0 differenze (escluso un elemento di History → Fetches
        che cambia da solo tra due esecuzioni: il caricamento asincrono
        corre contro il cambio di scheda).
     3. **Utility in `css/utilities.css`.** Le 148 classi `u-*` e i toni
        `[data-tone]` passano in un foglio nuovo, caricato **per ultimo**
        (in `index.html`, `sw.js` SHELL_FILES, `CACHE_NAME` v20). Metterlo
        al posto di `styles.css` avrebbe girato 578 coppie di regole, per
        ultimo 253, quasi tutte la stessa tinta con due nomi (`--muted` /
        `--text-muted`): il controllo statico (coppie di regole il cui
        ordine si inverte, proprietà che si toccano, stessa importanza e
        specificità, elementi in comune nella sonda a runtime, valori
        diversi anche dopo gli alias del bridge) ne lascia 2. Una era reale:
        `.s-section > .s-row` di `settings.css` azzera già `u-pl-16` su
        cinque righe di Settings, che perdono la classe in `index.html`
        (le righe di `#ext-quality-rows`, non figlie dirette, la tengono).
        `test_frontend_inline_styles.py` legge `css/utilities.css`.
        Confronto: 0 differenze.
     4. **Liste di selettori divise per vista.** 21 regole con una lista
        che tocca più fogli di destinazione (es. `.explore-grid,
        .artist-gallery-grid` sul telefono, i blocchi `forced-colors` e
        `prefers-reduced-motion` che nominano pulsanti di ogni vista)
        diventano regole consecutive con lo stesso corpo, una per foglio:
        così ogni override resta nello stesso file della regola base, dopo
        di essa. Non divise le liste con `:has()`, `:is()`, `:where()` o
        pseudo con prefisso, dove un motore che non le capisce scarta
        l'intera regola. Confronto: 0 differenze. (Due liste rimaste
        intere al primo giro sono divise in `baa50c8`.)
     5. **Spostamento, foglio per foglio, dall'ultimo al primo** (così un
        passo intermedio non inverte mai una coppia che il risultato finale
        non inverta già). Ogni regola va al foglio della vista dove i suoi
        selettori trovano elementi (sonda a runtime su tutti gli stati di
        prova), alla shell (`layout.css`), ai dialoghi/toast
        (`feedback.css`) o, se tocca più viste, a `legacy.css`; i selettori
        mai trovati seguono le classi già piazzate o i vicini. Le regole
        spostate vanno in cima al foglio, nell'ordine originale, coi loro
        commenti; un `@media` è diviso per foglio con lo stesso prelude.
        Il controllo statico sull'ordine finale lascia 45 coppie, tutte
        riviste a mano: elementi diversi (`svg` in pulsanti diversi), o
        risolte mettendo in cima a `legacy.css` le regole che devono restare
        prima dei campi generici o di `.act-btn` (`.api-modal-body input`,
        `.s-search input`, `.reg-remove-btn`, la ricerca della coda,
        `[data-dirty]` di Save), o l'anello di focus: `layout.css` lo dà a
        specificità (0,1,0) e prima vinceva sui controlli vecchi che
        disegnano un raggio proprio; dove questi ora caricano dopo, una
        regola alla stessa specificità ne ripete raggio/contorno.
        Strumenti in `side-session-notes/…/voce22-tools` (la cartella
        temporanea è stata svuotata a metà lavoro).
        - `views/logs.css` (`8ab9132`): 15 blocchi. 0 differenze.
        - `views/player.css`: 104 blocchi (Player, Now Playing, testi). 0
          differenze; gli stati di prova non aprono Now Playing, qui vale
          soprattutto il controllo statico.
        - `views/stats.css`: 21 blocchi. 0 differenze.
        - `views/explore.css`: 9 blocchi. 0 differenze.
        - `views/following.css`: 15 blocchi. 0 differenze.
        - `views/local.css`: 21 blocchi. 0 differenze.
        - `views/settings.css`: 134 blocchi. 0 differenze.
        - `views/queue.css`: 102 blocchi. 0 differenze.
        - `views/home.css`: 213 blocchi. 0 differenze.
        - `feedback.css`: 82 blocchi. 0 differenze.
        - `legacy.css`: 113 blocchi. 0 differenze.
        - `layout.css`: 113 blocchi (reset, `:root` v1, bridge, `@font-face`,
          shell, barra telefono). Gli `url()` dei font, relativi, diventano
          `../assets/fonts/…`: il primo confronto lo ha trovato (larghezze in
          `ch` diverse di 0,01px con il font di riserva). 0 differenze.
     6. **`styles.css` tolto** da `index.html` e da `sw.js` (`CACHE_NAME` v21):
        restavano solo due commenti. I commenti lunghi dei blocchi `@media`
        divisi (telefono, forced-colors, reduced-motion, tabella brani…)
        erano finiti in più fogli: il testo resta in uno, negli altri un
        rimando di una riga. Commenti di `app.js` e `index.html` puntano ai
        fogli nuovi. Confronto: 0 differenze.
     7. **Nomi v1 → v2, bridge tolto, font.** Ogni `var(--v1)` dei fogli
        diventa il token v2 a cui il bridge lo legava (`--muted` →
        `--text-muted`, `--accent-*` → `--signal*`, `--fs-*`, `--radius-*`…),
        il token della stessa famiglia con lo stesso valore (`--sp-4` →
        `--space-3`) o il valore stesso (`--sp-1` → `2px`, `--t` →
        `var(--motion) var(--ease)`). Restano variabili di `layout.css` solo
        quelle della shell, che le sue regole di stato cambiano (`--rail-w`,
        `--dock-h`, `--nav-h`…), e lo z-index. Nuovi token in
        `branding_tokens.py` (e `tokens.css` rigenerato): i pallini della
        finestra e `brand-deep` tra i fissi; `scrim`, `overlay`, `chevron`
        per tema (`V2_THEMED`). `--tracking-label` diventa `0.12em`: il
        valore v1 vinceva su quello v2 (`.14em`) e la GUI mostrava già
        quello. Tolti Inter e Fraunces (`@font-face` e file): nessuna regola
        li usava più. Test: `test_brand_tokens.py` controlla che nessun
        foglio nomini un token v1; `test_brand_tokens_v2.py` accetta
        `rgba(var(--…-rgb), α)` (un token, non un colore crudo) e le
        variabili di `layout.css` e quelle che `app.js` imposta su un
        elemento. Confronto: 0 differenze sulle proprietà reali (le custom
        property v1 spariscono, com'è giusto). Suite completa: 2331 passati.
        **Voce 22 fatta.**
   **Prossimo:** Fase 4 (TUI, voci 23–26) — fatta, vedi sotto.
0. **Fasi 4, 5 e 6 fatte** (2026-11-12), su `redesign`, commit locali non
   pushati. Prima, fuori dal redesign, la **issue #277** (playlist):
   - `e74767e` — un brano fallito ricorda la playlist (colonna
     `failed_tracks.playlist_name`, migrazione db v8) e il retry lo rimanda
     nella cartella della playlist e nella sua M3U, non sciolto nella radice.
   - `cc1846e` — un brano trovato in una *Existing library folder* (#272)
     fuori dalla libreria non finisce più nella M3U come `../../altrove/…`
     (Navidrome non lo segue): viene messo nella cartella della playlist con
     un hard link (o una copia su un altro disco). Dentro la libreria resta
     il collegamento relativo di #267.
   Redesign:
   - **voci 23–24** (`cf3340b`): TUI con intestazione su una riga
     (`branding.header_line`), badge come letture tra parentesi
     (`[HI-RES]`, mai pillole piene), bordi `solid` ovunque, legende a
     `$text-muted` che diventano segnale sul pannello attivo; temi
     `mornye-console-dark/light` registrati per primi e di default; la
     coppia teal resta nel giro di `t`.
   - **voce 25** (`bdb3fb4`): passata sui 12 pannelli a 80×24 e 120×40,
     scuro e chiaro (`tests/visual/capture_tui.py`, ora sui temi console).
     Intro che vanno a capo, pulsanti larghi quanto l'etichetta (la History
     a 80×24 non taglia più), pulsanti warning/success a filetto: nel tema
     console `$warning` è il segnale e sembravano un secondo primario.
   - **voce 26 / F9** (`6c4fce4`): il tema scelto con `t` è salvato in
     `~/.mornye/tui-theme` e riletto all'avvio; `MORNYE_TUI_THEME_FILE` lo
     sposta (i test lo puntano in una cartella temporanea, `conftest.py`).
   - **voce 27** (`df01cbb`): `theme-color`, manifest e finestra pywebview
     sul fondo v2 (`#0d0d0d` / `#f2f2f0`). Logo invariato: una versione mono
     non è stata fatta.
   - **voce 28** (`3f035aa`): gli accenti cambiano solo il segnale
     (`css/accents.css`, quattro token `--signal*` per accento, tolti i
     preset da `styles.css`); ambra è il default e il primo del selettore;
     un `teal`/`green` salvato (vecchio default non toccato) passa ad ambra
     una volta, `BRAND_VERSION` 3.
   - **voce 29** (`761acb8`): `docs/site.css` sulla palette console
     (grafite, ambra, JetBrains Mono, raggi 3px, niente bagliori teal);
     `CHANGELOG.md` aggiornato. README e guide non hanno screenshot
     dell'app: niente da sostituire.
   - **voce 30** (`eed9df8`, `4b28c76`): contrasto AA di ogni accento in
     entrambi i temi, con test (chiaro: testo green più scuro, inchiostro
     scuro su orange e cyan); un solo anello di focus da 1px nel segnale
     (`layout.css`) al posto del 2px di `styles.css`; percorso col Tab di
     Home, Settings e Queue: ogni fermata ha anello o bordo segnale.
     Nessuno sbordo orizzontale a 375px e 1280px, scuro e chiaro, in tutte
     le viste (corretta la toolbar del Player sul telefono).
   - **voce 31 — verificata solo in parte, qui non si può fare di più:**
     macOS sì per la GUI web (Chromium del pannello). **Da fare a mano:**
     finestra pywebview su macOS (WKWebView), Windows (WebView2), Linux
     (WebKitGTK); `--web` in Docker (qui il demone Docker è spento); PWA
     su un telefono vero (installazione, barra di stato con `theme-color`).
     Controllo statico dei fogli nuovi: nessun `@layer`, `@container`,
     `color-mix()`, `oklch()`; usano `:has()` in 4 regole (WebKitGTK ≥ 2.36,
     Safari 15.4, Chromium 105) — senza, Home lascia "Fetch" primario
     accanto alla scheda album e la coda mostra una riga vuota sotto i
     brani in attesa/errore: estetico, nessuna funzione persa;
     `text-wrap: pretty` viene ignorato dove manca.
   **Aperte allora:** F7, riorganizzazione di Settings, quality display
   dell'album, toast "fetching metadata…" — chiuse nel punto 7; resta la
   verifica con dati veri (Jellyfin, scansione Local, seguiti, download).
0. **Fase 2 completa** — sul branch **`redesign`** (creato da `2.0.6` dopo
   i mock, come da §5: dalla Fase 2 in poi un branch dedicato e un rilascio
   unico). Commit locali, non pushati.
   - **Voce 7, mock** (`0e9fc45`, su `2.0.6`): `docs/mocks/home.html`,
     `queue.html`, `tui-download.html` (generato da `make_tui_mock.py`, 120×40).
     **Approvati dall'utente.** Regole emerse: la qualità per riga è una
     lettura grigia; l'ambra solo per album, selezione, download in corso e
     una sola azione primaria per schermata; gli errori in un solo pannello
     "Failed"; Jellyfin nel rail come *Library → Server*.
   - **Voce 8, token v2** (`0430a77`): in `branding_tokens.py`, accanto ai v1
     che restano finché le Fasi 3–4 non li sostituiscono. Primitivi
     (`grey-*`, `paper-*`, `amber-*`, verde/rosso per tema) e semantici per
     tema (`ground`, `surface`, `raised`, `selected`, `rule`, `rule-strong`,
     `control-border`, `text`, `text-2`, `text-muted`, `signal`,
     `signal-strong`, `signal-text`, `signal-ink`, `ok`, `error`); spazi su
     griglia 4px, dimensioni, raggi, scala tipografica unica, elevazione
     piatta, durate ≤120ms. Il segnale ha tre ruoli perché in chiaro l'ambra
     piena non regge 3:1: riempimento, grafica/focus, testo.
     `frontend/css/tokens.css` è **generato** (`python -m
     mornye.branding_tokens`) e un test lo confronta byte per byte.
     `v2_textual_theme_kwargs()` costruisce i temi TUI, **non ancora
     registrati** (voce 24). `tests/test_brand_tokens_v2.py`: parità,
     contrasto (4.5:1 testo, 3:1 controlli) in entrambi i temi, un solo
     segnale, griglia, movimento, elevazione, un solo font, componenti solo
     su token semantici.
   - **Voce 9, styleguide** (commit sotto): `frontend/css/base.css` e
     `components.css` sono i file **veri** della Fase 3 (bottoni, campi,
     check/switch, display di qualità, parole di stato, meter, pannello con
     legenda, tabella, rail, tab, notice — sostituisce i toast —, fault,
     blank, skeleton fermo, dialog — sostituisce `confirm()` —, titlebar,
     dock), non ancora caricati dall'app. La pagina sta in
     **`docs/styleguide.html`** e non in `frontend/` come diceva la voce 9,
     così non finisce nel pacchetto; si apre servendo la radice del repo
     (configurazione `mocks`). Verificata in scuro, chiaro e a 375px (nessuno
     scorrimento orizzontale).
   **Da sapere per la Fase 3:** `--text` e `--surface` esistono sia nei
   token v1 (`styles.css`) sia nei v2 con valori diversi; se i due fogli
   convivono vince l'ordine di caricamento. Proposta per la voce 10: come
   primo passo ridefinire i nomi v1 come alias dei v2 (es. `--surface2:
   var(--raised)`), così tutta la GUI passa alla grafite in un colpo e le
   viste si rifanno poi una per una. Nomi di classe nuovi verificati liberi
   in `styles.css` (per questo `notice`/`blank`/`switch` e non
   `toast`/`empty`/`toggle`).
   **Prossimo:** Fase 3, voce 10 (shell: rail, titlebar, dock, player bar).
1. **Fase 1 completa** (voci 4–6) — l'aspetto è identico per costruzione:
   stili calcolati di ogni elemento di `index.html` senza JS (1472 elementi ×
   desktop/telefono × scuro/chiaro) e della GUI in esecuzione (ogni vista e
   scheda delle impostazioni, 52.449 stati) confrontati prima/dopo con 0
   differenze; screenshot uguali alla baseline. Copertura a runtime: 31 dei
   107 tag generati da `app.js` sono comparsi nel giro; per gli altri un
   controllo statico delle regole concorrenti ha trovato un solo caso reale
   (input password delle impostazioni estensione), lasciato inline.
   **Prossimo:** le voci di §7.4 previste "dentro le Fasi 0–1" e non ancora
   fatte — F1 (CSP: prima `data-action` al posto dei 166 `onclick=`), M6
   (stringhe in un dizionario), M2 (`create_app`), M4 (test JS con
   `node:test`) — oppure direttamente la Fase 2 (mock, token v2,
   styleguide).
   Suite completa dopo la Fase 1: 2253 passati, 357 saltati.
   **Lasciato apposta:** colori dentro `app.js` (tinta di default di Now
   Playing in `npApplyColors`, `theme-color`), da portare ai token nella
   voce 8.
2. **§2 tutta decisa** (2026-11-12): direzione A, JetBrains Mono come unico
   font, segnale ambra, logo invariato fino alla Fase 5, tema chiaro
   derivato. Nessuna scelta aperta prima della Fase 2.
3. **F1, F6, F7, M1–M6, N1–N5** — vedi il punto 7 (F9 fatta nella voce 26).
4. **F3, resto**: restano ~30 avvisi `ASYNC240`, quasi tutti un singolo
   `exists()`/`getsize()` su disco locale. Lasciati di proposito.
5. **Jellyfin con un server vero** — vedi sopra.
6. **Note emerse:** a 80×24 il pannello History della TUI tagliava il testo
   a destra (corretto nella voce 25). `mypy mornye` nella venv locale 3.14
   si ferma su uno stub di `numpy` (`python_version = "3.11"`); con
   `--python-version 3.12` resta 1 errore preesistente in
   `core/hires_check.py:469`. `hc-summary` è aggiornato da `app.js` ma non
   esiste più nell'HTML. Suite completa dopo J4: 2249 passati, 357 saltati
   (l'unico fallimento era un test da aggiornare, fatto nello stesso commit).
7. **Chiusura delle voci aperte** (2026-11-16, sessione dedicata, branch
   `claude/plan-rest-3e589f` da `2.0.7` a `bd1d93c`, un commit per voce,
   non pushati). Confronto degli stili calcolati con lo strumento della
   voce 22 (140 scenari), base presa da un checkout separato di `bd1d93c`.
   - **F7 · accessibilità** (commit sotto). (a) Gli 11 `confirm()` nativi
     diventano `askConfirm(domanda, {title, ok, danger})` → `Promise<bool>`,
     che apre `#confirm-modal`: lo stesso dialogo a filetto degli altri
     (`api-modal`), stretto (`.confirm-card`, `feedback.css`), su telefono
     come foglio dal basso. Escape, sfondo e Cancel rispondono no; il Tab
     resta nei due pulsanti; alla chiusura il focus torna dove era. Le
     domande distruttive (cancella profilo/file/cronologia, svuota coda…)
     hanno il pulsante `danger` e partono su Cancel, le altre sul pulsante
     di conferma. `switchView()` resta sincrona: con modifiche non salvate
     chiede e, su "Leave", richiama se stessa. (b) 18 elementi cliccabili
     non controlli (pallini della finestra, versione e badge
     d'aggiornamento ×5, dock, info del player, righe coda di Now Playing,
     risultati di ricerca, recenti, righe di History, card di Explore e
     degli album dell'artista, voci del selettore cartelle, righe della
     libreria del Player) hanno `tabindex="0"`, `data-activate` e un ruolo;
     un solo `keydown` in `app.js` trasforma Enter/Spazio sull'elemento
     stesso nel suo clic (`makeActivatable()` per quelli creati in JS).
     Lasciati: gli sfondi dei dialoghi (si chiudono con Escape e i
     pulsanti) e le righe dei testi di Now Playing (centinaia di fermate di
     Tab; il clic resta una scorciatoia del mouse). (c) `outline: none`:
     una sonda (ogni controllo visibile di ogni vista, scheda e stato di
     prova, transizioni spente, elemento e due antenati) trova un solo
     controllo che a fuoco non cambia aspetto — la casella "Reset seen
     history" di Following, presa da `#view-following input:focus` che le
     toglieva l'anello per colorarle un bordo che una casella nativa non
     disegna; ora la regola esclude caselle/radio come le altre della
     vista. Gli altri `outline: none` sono campi che mostrano il fuoco sul
     bordo. (d) `<img>` senza `alt`: due nel markup generato (miniature
     della ricerca, copertina proposta in Local, ora anche passata da
     `escHtml`). Test: `tests/test_frontend_a11y.py`. Confronto: cambiano
     solo gli elementi nuovi del dialogo (nascosto); 0 differenze sul resto.
     Provato con Playwright: dialogo in scuro/chiaro a 1280 e 375, Tab,
     Escape, Enter su una card dei recenti.
   - **Toast "fetching metadata…" durante la ricerca** (commit sotto).
     Causa: in modalità ricerca `onFetch()` chiama `setFetchingState('start')`
     (che apre l'avviso in basso a sinistra e blocca i recenti con
     `fetching-disabled`), ma `app_handle_provider_search_results` in quel
     ramo azzerava solo `isFetchingData` e il pulsante Fetch: l'avviso e il
     blocco dei recenti restavano finché un fetch successivo non li
     sostituiva. Ora i risultati chiamano `setFetchingState('hide')`. Stesso
     difetto svuotando il campo durante una ricerca: la risposta arriva
     scartata come vecchia e nessuno chiudeva l'avviso; ora lo chiude lo
     svuotamento. Riprodotto sul checkout di base e verificato dopo
     (Playwright: 1 avviso → 0, recenti sbloccati).
   - **Orfano `hc-summary`** (commit sotto): `updateStatusSummary()` e le
     sue 7 chiamate tolte; scrivevano in un'etichetta che non esiste più e
     il conteggio lo mostra già la pillola `#status-overall`. Nessun
     cambio di comportamento; `KNOWN_ORPHANS` ora è vuoto.
   - **F6 · `app.py`** (commit sotto): 0 `except Exception: pass` (erano
     21). `_push()` non solleva mai: un invio fallito va a
     `_push_failed()`, log a debug sul logger `mornye_gui.app` (fuori da
     `mornye`, il cui `UILogHandler` rimanderebbe il messaggio attraverso
     lo stesso invio), con un blocco di rientro per thread. Tolti i 12
     `try/except` attorno a sole chiamate `_push`; gli altri 8 (versione da
     `pyproject`, massimizza, playcount, cronologia dei fetch, monitor
     delle statistiche, ricerca del frontend) loggano a debug con
     traceback; `remove_history_item` dice all'utente che non è riuscito
     (`warn`). Restano ~50 casi negli altri moduli (`launcher.py` 4, poi 1–3
     per file).
   - **Riorganizzazione di Settings** (voce 14, commit sotto; proposta A
     scelta dall'utente). Solo spostamenti di sezioni intere: id, campi e
     salvataggio invariati. General = Appearance, Account, Logging · nuova
     scheda **Sources** (`#tc-sources`, icona a onde) = Audio Sources,
     Quality, Auth & APIs, Self-hosted Endpoints (da Extensions) ·
     Download = Performance, Retry & Loop, Transcoding, Post-Download
     Action, Profiles · Files = Download Location, Filename Format, Folder
     Organization, Network Destinations (da Download) · Metadata, Status,
     Extensions come prima (Extensions senza gli endpoint). A ≥1900px è
     Files a restare su una colonna (ci sta il form largo delle
     destinazioni), non più Download. Controllo: nessuna riga del markup
     persa (multinsieme delle righe prima/dopo). Verificata in scuro a 1280
     e in chiaro a 375, nessuno sbordo. Lo strumento di confronto ora
     conosce la scheda `sources`.
   - **Display di qualità dell'album** (commit sotto; opzione (a) scelta
     dall'utente). `#album-quality` sotto artista/data nella scheda album:
     all'apertura mostra la qualità chiesta in Settings, attenuata e
     marcata (`REQ · LOSSLESS`, `REQ · HI-RES LOSSLESS`, `REQ · DOLBY
     ATMOS`); quando un brano di quella scheda risulta `completed` nelle
     statistiche (`app_update_download_stats`, confronto per id), il primo
     file viene letto con la nuova API `probe_audio_quality(path)` (solo
     `probe_audio`, senza spettrogramma; percorsi approvati come le altre;
     nell'allowlist di `webapp.py`) e la lettura diventa reale sul segnale:
     `24 / 96 · FLAC`, o `320 KBPS · MP3` per un file con perdita. Non
     considera la qualità per servizio delle estensioni (`ext-quality`):
     il "richiesto" è il livello globale. Verificato con la scheda di prova
     e una risposta simulata della sonda, scuro a 1280 e chiaro a 375. Test:
     `tests/test_album_quality_readout.py` (un WAV vero).
   - **F1 · Content-Security-Policy** (commit sotto; prima `5fc8a2d`:
     `probe_audio_quality` mancava in `REMOTE_METHODS` di `web-shim.js`,
     quindi in `--web` il display di qualità restava "REQ"). (1) I 271
     handler inline (208 in `index.html`, 62 nel markup di `app.js`, 1 in
     `toast-system.js`) diventano `data-on<evento>="…"`, stesso codice.
     `frontend/inline-handlers.js` li lega senza `eval` né `new Function`:
     un tokenizer e un parser a precedenze per il sottoinsieme che usano
     (chiamate, membri, `this`, `event`, letterali e array, `! - + === !==
     == != < > <= >= && ||`, assegnazione, `if`, `return`), compilato una
     volta per stringa; un listener vero sullo stesso elemento (stessa
     fase dell'attributo), `return false` → `preventDefault`,
     `this.onerror = null` toglie il listener (niente cicli sui fallback
     delle immagini). Il markup aggiunto dopo è legato da un
     `MutationObserver` prima che i suoi eventi possano arrivare. I nomi
     sono globali di `window`; l'unico `const` usato dagli handler, `$`, è
     registrato con `MornyeHandlers.expose()`. Un handler che non si
     analizza resta morto con un errore in console: un test (node) li
     analizza tutti. (2) Gli script inline escono in file: `boot-head.js`
     (fogli con `?_=` e tema/accento prima del primo disegno),
     `boot-body.js` (classi sul `<body>`), `boot-tail.js` (script dell'app
     e service worker); `window.__MORNYE_WEB_MODE__` lo imposta
     `web-shim.js`, non più uno script iniettato. `sw.js` li elenca
     (`CACHE_NAME` v22). (3) `webapp.py`: la pagina manda
     `Content-Security-Policy` con `script-src 'self'` (niente
     `unsafe-inline`/`unsafe-eval`), `object-src 'none'`, `base-uri
     'none'`, `form-action 'self'`, `frame-ancestors 'none'`,
     `connect-src` limitato a questo host (anche `ws://`/`wss://`),
     immagini e audio anche da `https:`/`http:` (copertine e anteprime dei
     provider), `style-src` con `'unsafe-inline'` (gli `style=""` e
     `el.style` restano; un hash non li copre) più `X-Frame-Options:
     DENY`; ogni risposta `X-Content-Type-Options: nosniff` e
     `Referrer-Policy: same-origin`. Verificato con Playwright sotto la
     politica vera: clic reali sul rail e sulle schede di Settings, ripiego
     `onerror` di un'immagine rotta, chiusura di un toast, dialogo di
     conferma — 0 violazioni (`securitypolicyviolation`), nessun errore di
     pagina. Confronto degli stili (144 scenari) contro `5fc8a2d`: solo uno
     `<script>` in meno nel `<body>`, 0 differenze sugli elementi. Test: `tests/test_web_csp.py` (nessuno script/handler inline,
     intestazioni, ogni handler analizzabile, semantica
     dell'interprete); `test_webapp_pwa.py` aggiornato. **Da provare a
     mano:** finestra pywebview (stessi file, nessuna CSP: deve solo
     comportarsi come prima) e `--web` dietro un proxy che incornicia la
     pagina (ora rifiutato da `frame-ancestors`).
   - **M1 · `app.js` in moduli — rinviata, con le misure per farla.** La
     finestra desktop carica la pagina dal server HTTP di pywebview
     (`webview.start(http_server=True)`), non da `file://`: i moduli ES
     si caricherebbero su tutti e tre i backend. Due ostacoli concreti:
     (a) su Windows `mimetypes` legge il registro e può servire `.js` come
     `text/plain`, che un `<script type="module">` rifiuta (uno classico
     no): serve forzare il tipo nel server di pywebview o in `webapp.py`
     prima; (b) i moduli non mettono le funzioni su `window`, e gli
     handler `data-on*` le cercano lì (F1): ogni modulo dovrebbe
     esportarle con `MornyeHandlers.expose()`. Una divisione in script
     *classici* per vista conserva lo scope globale condiviso, ma sposta
     il sollevamento delle `function` da tutto `app.js` al singolo file:
     una chiamata fatta al caricamento verso una funzione di un file
     successivo, se protetta da `typeof f === 'function'`, smetterebbe in
     silenzio. Senza un parser JS (qui non c'è: niente acorn/esprima,
     nessun download) non si può escludere in modo statico, e il
     confronto a runtime non vede i rami solo-desktop. Ordine proposto:
     (1) tipo MIME forzato; (2) divisione classica per sezioni `// ──`,
     con un controllo statico (acorn) delle chiamate al caricamento;
     (3) moduli vista per vista, con `expose()`.
   - **M2 · le tre funzioni più intricate** (commit sotto). Complessità
     (ruff C901): `create_app` 92 → 17, `build_v1_router` 53 → 2,
     `amain` 73 → 46. `create_app`: otto gruppi di rotte diventano
     funzioni di modulo (`_install_token_gate`,
     `_register_multiuser_routes`, `_register_ops_routes`,
     `_register_method_dispatch`, `_register_file_routes`,
     `_register_websocket`, `_register_v1_api`, `_register_frontend`),
     chiamate nello stesso ordine (l'ordine delle rotte conta: il
     catch-all `/api/{method_name}` e il mount statico restano dopo).
     Parametri ricavati con `symtable` (le variabili libere di ogni
     funzione annidata); unico ritorno: `_register_v1_api` restituisce il
     `DownloadService`, che la coda multi-utente in cima a `create_app`
     legge quando un job parte. `build_v1_router`: sette gruppi
     (`_instance_routes` … `_library_routes`). `amain`: i 23 comandi a
     uscita anticipata (`--gui`, `--tui`, `--web`, `--web-user-*`,
     `--stats`, `--health-check`, `--trust-key-*`, `--split-cue`,
     `--dedup-*`, `--upgrade-library`, …) diventano `_cli_*()`; resta in
     `amain` il percorso di download (lettura degli argomenti e
     `_run_once`), da spezzare a parte. Nessun cambio di comportamento;
     test web/API/CLI/TUI verdi.
   - **M3 · `ruff` allargato, primo lotto** (commit sotto). `select`
     aggiunge `RUF006`, `B`, `ASYNC`, `UP`; ignorati con motivo scritto in
     `pyproject.toml`: `ASYNC109` (il parametro `timeout` è la convenzione
     del codice), `ASYNC240` (il resto di F3: un solo `exists()`/`stat()`
     su disco locale; lasciato apposta), `UP042` (`StrEnum` cambia `str()`
     dei membri, che finiscono in config e API), `B009`/`B010`
     (`getattr`/`setattr` con nome fisso dove l'attributo non è dichiarato:
     la forma che scrive l'auto-fix la rifiuta mypy). `B008` accetta i
     marcatori di FastAPI (`Body`, `Depends`, …). Correzioni: `UP`
     automatiche (`datetime.UTC`, annotazioni senza virgolette, import da
     `collections.abc`, `TimeoutError`); 12 `zip()` con `strict=False`
     esplicito (comportamento invariato); 6 `raise … from`; i 7 `B023` con
     il valore del giro legato (`functools.partial` nelle chiamate SMB e
     WebDAV, argomento predefinito nei due helper del provider); i due
     `ASYNC110` del codice sono attese su un lock/evento di *thread*, non
     sostituibili con un `asyncio.Event`: commentati e marcati; la lettura
     dei 12 byte di intestazione della WebP animata va in `to_thread`.
     **Un bug vero (B025):** in `download_service.py` il ramo che aggiunge
     "Retry identifier: …" a un errore di consegna su destinazione di rete
     stava dietro un `except Exception` precedente e non girava mai; ora i
     due rami sono uno solo e l'utente vede l'identificativo per
     riprovare (test nuovo in `test_architecture_foundation.py`, verificato
     che fallisce con un'attesa sbagliata). Nei test: `B017` con
     `WebSocketDisconnect`, due eccezioni per file dove l'attesa bloccante
     è voluta. `mypy` resta ai 3 errori preesistenti
     (`core/env_settings.py:109`, `application/download_service.py`
     ~246–247), uguali sul checkout di base. Prossimi lotti: `SIM`, `PERF`,
     `BLE`/`S110` (i ~50 `except Exception: pass` fuori da `app.py`), poi
     `RUF` intero.
   - **M4 · test per il JavaScript** (commit sotto). `tests/js/*.test.mjs`
     con `node:test`, senza dipendenze; li lancia `tests/test_js_unit.py`
     (`node --test`, saltato senza node). `app.js` non si può importare
     (al caricamento tocca il DOM): `tests/js/load.mjs` ritaglia dal
     sorgente le funzioni di primo livello per nome e le esegue in un
     contesto `vm`. Coperti: `escHtml`, `httpUrlOrNull` (rifiuta
     `javascript:`/`data:`/`file:`), `formatEta`, `normalizeHistoryUrl`
     (non scambia `open.spotify.com.evil.com`), `fmtTime`, `describeError`
     (un tipo per errore), `formatAudioQuality`, e l'interprete degli
     handler di F1 (chiamate, `if`, `expose()`, `return false`, errori mai
     silenziosi, nessun `eval`). 12 test. Giro di fumo nel browser:
     `tests/test_gui_smoke.py`, a richiesta (`MORNYE_TEST_BROWSER=1`,
     extra `visual`): `mornye --web` su una HOME vuota, ogni vista e ogni
     scheda di Settings con clic veri a 1280 e 375, sotto la CSP vera;
     fallisce su errori di pagina, di console o violazioni CSP. Passa.
   - **N2 · "Copia come comando CLI"** (commit sotto). Pulsante "CLI"
     (icona prompt) nelle azioni della scheda album e brano singolo; copia
     negli appunti la riga `mornye …` che fa lo stesso download con le
     impostazioni attuali. Backend: `cli_preview.gui_command(url, cfg,
     cartella)` mette `buildConfig()` sopra i default completi della TUI
     (`ConfigState`), con gli stessi default `MORNYE_<SETTING>` di un
     download dalla GUI (`gui_env_defaults()`), e la passa a
     `format_command()` — la stessa resa del pannello Command della TUI,
     quindi solo i flag che differiscono dai default della CLI. Le chiavi
     solo-GUI (qualità per estensione, destinazione di rete, suoni) restano
     fuori; `lyrics` → `embed_lyrics`; nessun timeout se la GUI non ne
     manda. API `get_cli_command(url, config)` (allowlist e
     `REMOTE_METHODS`). Appunti: `navigator.clipboard`, poi la selezione
     di una textarea; se mancano entrambi il comando va in Logs. Una
     ricerca (non un link) non ha comando: avviso. Verificato con
     Playwright (album di prova, appunti letti: comando corretto; toast
     "Command copied"), scuro a 1280 e chiaro a 375. Test:
     `tests/test_cli_command_from_gui.py`. Nota: il suggerimento `data-tip`
     dei pulsanti sul bordo sinistro esce dallo schermo sul telefono (vale
     per tutti i pulsanti con `data-tip`, preesistente).
   - **M5 · soglia di copertura** (commit sotto). Misurata sulla suite
     completa (`5a7275b`, macOS, Python 3.14): **67%** (31.409 istruzioni,
     10.282 non coperte; 2348 passati). In CI `--cov-fail-under` passa da
     30 a 60: margine per i test che un runner salta (senza node, ffmpeg,
     estensioni vere). Da verificare al primo push: Linux 3.11–3.14 e
     macOS. Prossimo scatto: 65.
   - **N1 · palette comandi e scorciatoie** (commit sotto).
     `frontend/palette.js` (caricato dopo `app.js` da `boot-tail.js`, in
     `sw.js`, `CACHE_NAME` v23): Ctrl+K / ⌘K apre un dialogo in alto con
     33 comandi — le dieci viste, le sette schede di Settings, salva
     impostazioni, barra link/ricerca, fetch, cambio link/ricerca, copia
     comando CLI e scarica tutto (solo con una scheda aperta), drawer della
     coda, avvia/ferma, riprova i falliti, controllo provider, play/pausa,
     Now Playing, tema scuro/chiaro/sistema, rail. Filtro per parole in
     qualsiasi ordine; ↑/↓, Invio, Esc; il focus torna dove era. Fuori da
     un campo di testo `/` porta alla barra link/ricerca (come `/` nella
     TUI) e `?` apre la palette. Ogni comando chiama una funzione che
     `app.js` ha già: nessun comportamento nuovo. Aspetto in
     `feedback.css` (famiglia dei dialoghi: filetti, voce attiva con la
     barra segnale, scorciatoie come `kbd`). Verificato con Playwright a
     1280 scuro e 375 chiaro (apre, filtra "set fil" → Settings → Files,
     Invio, Esc, `/`), nessun errore. Test: `tests/test_command_palette.py`
     (ogni funzione chiamata esiste, ogni scheda esiste, le scorciatoie non
     scattano nei campi).
   - **N4 · densità regolabile** (commit sotto). Settings → General →
     Appearance → **Density**: Comfortable (default, invariato) /
     Compact. Compact mette `data-density="compact"` su `<html>` e
     `<body>`; `layout.css` toglie un passo a `--row` (32 → 28),
     `--row-head` (28 → 24) e agli spazi grandi (`--space-3…8`: 12/16/20/
     24/32 → 8/12/16/20/24), sempre sulla griglia di 4px; restano 4 e 8px,
     gli spazi dentro un controllo. Tutto ciò che è costruito sui token
     segue; le misure scritte in px nei fogli delle viste no (è il limite
     di questa prima versione). Salvata come l'accento: `localStorage`
     (`mornye-density`, applicata in `boot-head.js`/`boot-body.js` prima
     del primo disegno), il blob delle impostazioni e `gui-settings.json`
     (nuova API `save_density`, valori ammessi controllati). Verificato con
     Playwright: scelta, `--row` 28px, sopravvive al ricaricamento, nessuno
     sbordo, scuro a 1280 e chiaro a 375. Test: `tests/test_gui_density.py`.
   - **N3 · parità TUI — Following fatto; Local ed Explore no** (commit
     sotto). Pannello nuovo `tui/following_view.py` (voce "Following" nel
     rail dopo Library, glifo ★/`*`, etichetta `--subscribe`): elenca le
     iscrizioni dello stesso archivio di GUI e `--subscribe` (nome, tipo,
     stato, intervallo, ultimo controllo, già viste, errore), segue un
     nuovo link (Invio nel campo o "Follow"; scarica nella cartella del
     pannello Download), mette in pausa/riprende e toglie quella
     selezionata. Solo letture/scritture SQLite locali; il controllo delle
     uscite resta allo scheduler e a `--subscribe-check` (rete e
     download). Senza l'estensione `utilities` il tipo di un link non si
     ricava e il pannello lo dice nella riga di stato, come la GUI.
     Verificato con un render a 120×40 (schermata SVG) e con pilot. Test:
     `tests/test_tui_following.py`. **Non fatti:** *Local* (tagging e
     conversione di file locali: scansione lunga, anteprima delle
     corrispondenze, applicazione per file — un flusso a più passi, non
     una tabella) ed *Explore* (il feed è una griglia di copertine; in
     terminale andrebbe ripensato come elenco di sezioni). Da fare ognuno
     con la sua sessione. Nota: `tests/visual/capture_tui.py` ora cattura
     13 pannelli; la baseline ne ha 12.
   - **M6 e N5 — rinviate insieme, con le misure.** Inventario
     (2026-11-16): `index.html` ha ~560 stringhe uniche (439 nodi di
     testo, 124 attributi `placeholder`/`title`/`aria-label`/`data-tip`/
     `alt`; ~2.300 parole); `app.js` almeno 340 letterali visibili (toast,
     stati, titoli, `textContent`), quasi tutti in template con
     interpolazioni e plurali. Un italiano che copre solo il markup
     statico darebbe un'interfaccia metà italiana e metà inglese (righe,
     toast, errori generati da JS) — la stessa ragione per cui la §5 vuole
     un rilascio unico. Piano proposto: (1) `frontend/i18n.js` con
     `t(chiave, variabili)` e plurali (`Intl.PluralRules`), dizionari in
     `frontend/locales/en.json`/`it.json`, attributi `data-i18n` /
     `data-i18n-attr` sul markup applicati da `boot-tail.js` prima di
     `app.js`; (2) estrazione automatica del markup statico (uno script
     che assegna chiavi stabili dall'`id`/contesto) con un test che rifiuta
     testo nuovo fuori dizionario; (3) `app.js` vista per vista, insieme a
     M1 (i moduli sono il momento naturale per passare ogni stringa da
     `t()`); (4) N5: `it.json` completo e selettore lingua in General,
     solo quando (1)–(3) coprono tutto. Testi del backend mostrati nella
     GUI (`self.log(...)`, errori delle API) restano in inglese: da
     decidere se tradurli o mostrarli come dettaglio.
   **Stato di chiusura (2026-11-16):** fatte F7, toast di ricerca,
   orfano `hc-summary`, F6 (`app.py`), riorganizzazione di Settings,
   display di qualità, F1, M2, M3, M4, M5, N1, N2, N4; N3 in parte
   (Following). **Ancora aperte:** M1 (piano sopra), M6 + N5 (piano
   sopra), N3 Local ed Explore, F6 negli altri moduli (~50 casi, lotto
   `BLE`/`S110` di M3), F3 resto (deliberato), Jellyfin con un server
   vero, voce 31 (pywebview su macOS/Windows/Linux, Docker, PWA), mypy:
   3 errori preesistenti (`core/env_settings.py:109`,
   `application/download_service.py` ~246–247).

### Da sapere prima di toccare qualcosa

- **Verifiche da rifare su CI.** Le modifiche a `ci.yml` (matrice 3.13/3.14,
  job macOS, mypy bloccante) non sono mai girate: `mypy` e `black` sono stati
  provati solo in locale (Python 3.14, black 26.5.1 contro il ≥26.10.1 che la
  CI installa). Al primo push va guardato l'esito.
- **28 file di test sono saltati di default** (`tests/conftest.py`,
  `_OPTIONAL_UTILITIES_TESTS`): girano solo con
  `MORNYE_TEST_REAL_EXT_DIR=1` e usano le estensioni installate in
  `~/.mornye/extensions`. Con quel flag, **5 test di
  `tests/test_search_mixin.py` falliscono già da prima** di questo lavoro
  (verificato con `git stash`): da indagare a parte.
- **Hook del progetto (GateGuard):** il primo `Bash` e ogni `Write` di un
  file nuovo vengono rifiutati finché non si dichiarano i "fatti" richiesti;
  basta riproporre la stessa operazione dopo averli scritti.

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
| Testo | **JetBrains Mono per tutto** — interfaccia, titoli, numeri e dati tecnici (deciso dall'utente, 2026-11-12). Inter e Fraunces escono, con i loro file in `assets/fonts/`. La gerarchia si fa con peso, dimensione e maiuscoletto, non cambiando famiglia |
| Etichette | Sezioni in maiuscoletto spaziato, come le serigrafie di un pannello |
| Movimento | ≤120ms, solo cambi di stato. Niente animazioni d'ingresso |

Tre elementi firma, identici nei due mondi:

1. **Il display di qualità** — `24 / 96 · FLAC` sul colore segnale,
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

### Default — **confermati dall'utente** (2026-11-12)
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
- **Tutto in monospazio.** Con JetBrains Mono ovunque il testo occupa più
  larghezza di Inter: titoli lunghi, colonne della tabella tracce e la
  Settings vanno provati presto (voce 9, styleguide) alle larghezze strette,
  con troncamento e `font-variant-ligatures: none` nei dati.
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

---

## 8. Ricerca tramite estensioni — cosa si è rotto e cosa è stato corretto

Partito da uno screenshot dell'utente: ricerca "dr" su *Amazon Music* →
"Could not reach the service", più due toast. Le cause erano quattro, tutte
riprodotte e corrette (verifica reale: ricerca "dr dre" su ogni sorgente).

1. **`fetch()` non esisteva nel bridge.** Il runtime mobile dà alle
   estensioni un `fetch(url, opts)` *sincrono*; `_bridge.js` non lo definiva,
   quindi il nome cadeva sul `fetch` asincrono di Node e ogni `res.status`
   era `undefined`. Amazon (9 chiamate) e YouTube Music (16) vedevano ogni
   richiesta come fallita. Ora `global.fetch` è costruito su `http.request`.
2. **Sorgenti scambiate per Deezer.** `site_for_extension` decideva il sito
   da `permissions.network`: amazon, spotify-web e ytmusic elencano
   `api.deezer.com` e venivano offerte come sorgenti "Deezer", con id e link
   Deezer sui risultati. Ora decide `urlHandler.patterns`; la lista di rete
   resta solo come ripiego per i manifest senza `urlHandler`.
3. **Nessun ripiego su `customSearch`.** Le estensioni con
   `searchBehavior.enabled` espongono `customSearch(query, {filter})` e non
   `searchTracks`. Ora la ricerca prova la prima e ripiega sulla seconda.
   Nuovo concetto: **sorgenti solo-ricerca** (`search_only_extensions`) —
   estensioni cercabili che non sono in `SITES`; i loro risultati tengono i
   link dell'estensione, che continuano a passare da `handleUrl`.
4. **Errore classificato male e mostrato tre volte.** Un metodo mancante era
   `NETWORK_ERROR` (quindi anche ritentato); ora è `UNAVAILABLE` con
   messaggio "the extension does not implement X()". Nel frontend l'errore
   di ricerca mostra solo la scheda, senza i due toast.

Esito sulle estensioni installate (3 risultati richiesti): bugs, deezer,
genie, melon, qobuz-web, tidal-web, amazon, apple-music, beatport, jiosaavn,
soundcloud, ytmusic → 3 tracce ciascuna con id e link corretti. jellyfin → 0
(nessun server configurato).

**Non verificato:** l'apertura e il download di un risultato Amazon/YouTube
dalla GUI (il link passa dalla strada `handleUrl`, che non è stata toccata).

**Per il redesign:** un errore = un solo posto sullo schermo. La regola va
nel componente "stato d'errore" della voce 9, non ripetuta vista per vista.

---

## 9. Jellyfin — come funziona e cosa manca

Estensione `jellyfin` 1.1.1 (`~/.mornye/extensions/jellyfin`), 720 righe.
Usa il **proprio server** dell'utente come sorgente di ricerca, download,
link, testi e home feed. Impostazioni: `server_url`, `username`,
`password` *oppure* `api_key`, più due pulsanti (`testConnection`,
`clearSession`). Login con `POST /Users/AuthenticateByName`, token in
`credentials`; download con `file.download` e header `X-Emby-Token`;
qualità `ORIGINAL` / `MP3_320` / `MP3_192` / `OPUS_128` (transcodifica lato
server).

**Analisi solo statica: non c'è un server Jellyfin con cui provarla.**

### Già supportato dall'host
`http.get/post`, `storage`, `file.download`, `utils.md5`,
`matching.compareStrings`, `gobackend.getLocalTime`, `log`, impostazioni
`string`/`secret`/`button` con `action` (backend
`api_mixins/extension_settings.py:175`, frontend `app.js:9079`),
`qualityOptions`.

### Corretto ora (ultimo commit, con test sul bridge reale)

| Mancava | Effetto | Correzione |
| --- | --- | --- |
| `credentials.store/get/remove` | token mai salvato: login a ogni avvio | `global.credentials` sopra lo storage cifrato, con prefisso riservato |
| `gobackend.sanitizeFilename` | eccezione nel download quando l'host passa solo la cartella | aggiunto a `utils`/`gobackend` |
| L'host chiama `handleUrl`, Jellyfin esporta `handleURL` | "No method: handleUrl": nessun link Jellyfin apribile | il bridge accetta entrambe le grafie |
| Pattern `#/details?id=` | letto come regex (il `?` rendeva la `s` opzionale) e ancorato all'host: non combaciava mai | i pattern che iniziano con `#` sono confrontati con il frammento dell'URL (`extensions/url_match.py`) |

### Da fare allora — **tutte e quattro fatte** (J1 `f5abb18`, J2 `00b7ac3`, J3 `33c8b4a`, J4 `641c0b1`; vedi §0)

| # | Cosa manca | Dove | Peso |
| --- | --- | --- | --- |
| J1 | **Accesso alla rete locale.** Il caso tipico è un server su `192.168.x.x`, che `_netguard.js` blocca. L'unica via oggi è `MORNYE_EXT_ALLOW_PRIVATE_NETWORK=1` (apre tutto, e solo da ambiente). Esiste già il meccanismo giusto — le impostazioni con `"grantsNetwork": true` (`sandbox.network_grants`) — ma `server_url` di Jellyfin non lo dichiara: il manifest presume un interruttore "Allow Local Network Access" che qui non c'è | backend: interruttore per estensione che concede gli host delle sue impostazioni-URL; frontend: il toggle in Settings → Extensions, con avviso | **bloccante** per l'uso reale |
| J2 | **`lyrics_provider`.** Nessun codice chiama `fetchLyrics` di un'estensione: i provider di testi sono solo quelli interni | `core/lyrics.py` + elenco provider in GUI/TUI/CLI | medio |
| J3 | **Home feed** (`capabilities.homeFeed`, `getHomeFeed`): sezioni "Recently added", "Most played", "Playlists"… Nessun supporto nell'host | backend: un metodo API; frontend: la vista *Explore* è il posto naturale | medio — conviene farlo **dentro la Fase 3** (voce 17), nel nuovo stile |
| J4 | **`skipMetadataEnrichment` / `skipBuiltInFallback`.** Ignorati: un brano della propria libreria verrebbe "arricchito" da fonti esterne e, se il server fallisce, cercato su altri provider | `application/download_engine.py`, `metadata_service.py` | piccolo, ma tocca il motore: servono test |

Da verificare con un server vero, in quest'ordine: `testConnection` dal
pulsante → ricerca → apertura di un link `…/web/#/details?id=…` → download
`ORIGINAL` → J1.

**Nota per il redesign.** Jellyfin è la prima estensione che è una
*libreria personale* e non un catalogo: nella direzione "console da studio"
merita di essere una sorgente di prima classe (rail: *Library → Server*),
non una voce nel menu della ricerca. Da decidere alla voce 7 (mock).
