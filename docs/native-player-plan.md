# Piano: motore audio nativo (libmpv) per il player di Mornye

Documento di lavoro, da riprendere in una sessione nuova. Riferimento
funzionale: [MPC-HC](https://github.com/clsid2/mpc-hc) con LAV Filters, per
la sola parte audio.

Righe di codice citate: verificate sulla versione 2.2.0 (novembre 2026).

**Obiettivo:** il player di Mornye fa da sé quello che oggi richiede
MPC-HC + LAV: **bitstream** AC-3/E-AC-3/DTS/TrueHD verso coassiale o HDMI,
scelta del **dispositivo di uscita**, **modalità esclusiva / bit-perfect**,
poi gapless, ReplayGain, ritardo, velocità, A-B, equalizzatore e mixaggio.
L'interfaccia del player resta quella attuale; cambia solo chi produce il
suono.

**Fuori dal piano:** tutto il lato video di MPC-HC (renderer, sottotitoli,
DVD, shader, filtri DirectShow). È un player musicale.

---

## Stato

| Fase | Stato |
| --- | --- |
| 0 · Adattatore `playerEngine` con il solo motore browser | da fare |
| 1 · Motore mpv + bitstream, dispositivo, esclusiva | da fare |
| 2 · Gapless, ReplayGain, ritardo, velocità, A-B | da fare |
| 3 · Equalizzatore, canali di uscita, downmix, normalizzazione | da fare |
| 4 · Statistiche e scorciatoie in stile MPC-HC | da fare |

---

## Perché serve un motore nativo

Il player oggi è un unico `<audio id="player-audio">`
(`mornye/frontend/index.html`) dentro la finestra pywebview (WKWebView su
macOS, WebView2 su Windows). Il motore web **decodifica sempre in PCM** e lo
consegna al mixer di sistema: il bitstream è impossibile, il dispositivo di
uscita è quello di sistema, niente modalità esclusiva.

Caso reale che ha originato il piano: impianto collegato via S/PDIF
(coassiale da una Asus Xonar senza Dolby Digital Live, oppure HDMI → TV →
ottico). L'S/PDIF porta solo stereo PCM o Dolby Digital/DTS 5.1 compressi:
il 5.1 arriva all'impianto solo se il player manda l'AC-3 **in bitstream**.
Mornye produce già la copia AC-3 dei download E-AC-3
(`core/transcode.py::convert_dolby_to_ac3_async`, attiva di default), ma
per farla arrivare all'impianto oggi serve MPC-HC.

## Cosa c'è oggi (punti di aggancio)

- `mornye/frontend/app.js`:
  - `loadPlayerTrack(index)` (~riga 8456): chiama
    `pywebview.api.prepare_playback(path, local_url)`, assegna `audio.src`,
    `audio.play()`, poi `fillNowPlaying` e `playerMediaMetadata`.
  - `playerToggle`, `playerNext`, `playerEnded`, `playerPrev`, `closePlayer`
    (~8550–8580); coda e ordine in `playerQueue`, `playerOrder`, `playerPos`,
    `playerRepeat`, `playerShuffle`, `playerBuildOrder`, `startPlayback`.
  - Media Session (`initMediaSession`, ~8520): tasti multimediali e Now
    Playing del sistema.
  - Testi sincronizzati: `npUpdate`, `npTick`, `npSyncLyrics`,
    `npAudioTimeForLyrics` leggono `currentTime` dell'elemento audio.
  - **63 riferimenti** a `$('player-audio')` / proprietà dell'elemento audio
    (`grep -n "player-audio\|audio\.\(currentTime\|volume\|src\|addEventListener\|duration\|paused\|playbackRate\|muted\)"`).
- `mornye/api_mixins/library_tools.py::prepare_playback` (~riga 211):
  approva il percorso, sceglie il file da servire tramite
  `core/playback.py::playable_file_async` (copia FLAC per i formati che il
  motore web non decodifica: `needs_lossless_copy`), etichetta di qualità
  `quality_label`, URL sul media server locale (`core/media_server.py`).
- Eventi dal backend alla pagina: `mornye/app.py` (~riga 327) usa
  `window.evaluate_js("window.<fn>(...)")` in desktop; `mornye/webapp.py` ha
  il canale equivalente per `--web`.
- Esposizione dei metodi API: `ALLOWED_METHODS` in `mornye/webapp.py` e
  l'elenco in `mornye/frontend/web-shim.js`; il contratto DOM in
  `tests/frontend_dom_contract.json` (rigenerabile con
  `MORNYE_UPDATE_DOM_CONTRACT=1 pytest tests/test_frontend_dom_contract.py`).

## libmpv: verificato

- macOS: `brew install mpv` fornisce `/opt/homebrew/lib/libmpv.2.dylib`
  (mpv 0.41.0 provato).
- Windows: `libmpv-2.dll` (build ufficiali di mpv / shinchiro) accanto
  all'eseguibile o nel PATH.
- Binding Python: [`python-mpv`](https://pypi.org/project/python-mpv/) 1.0.8,
  ctypes puro, nessuna compilazione.
- Opzioni presenti in 0.41 e necessarie al piano: `audio-device`
  (`mpv --audio-device=help` elenca i dispositivi reali), `audio-spdif`
  (`ac3,eac3,dts,dts-hd,truehd`), `audio-exclusive`, `gapless-audio`,
  `replaygain` (+ `replaygain-preamp`, `replaygain-clip`), `audio-delay`,
  `speed` + `audio-pitch-correction`, `ab-loop-a/b`, `audio-channels`,
  `af` (filtri ffmpeg: `equalizer`, `superequalizer`, `dynaudnorm`,
  `loudnorm`, `pan`), e **`media-controls`** (default `yes`), che gestisce
  da solo Now Playing e tasti multimediali su macOS e Windows.
- Proprietà da osservare: `time-pos`, `duration`, `pause`, `idle-active`,
  `eof-reached`, `playlist-pos`, `audio-codec-name`, `audio-params`,
  `audio-out-params`, `current-ao`, `audio-device-list`.

## Architettura

1. **`mornye/core/native_player.py`** (nuovo): una sola istanza `mpv.MPV`
   (thread di eventi di python-mpv), creata in modo lazy. Comandi: load
   (con coda), play/pausa, seek, volume, mute, avanti/indietro, stop.
   Osserva le proprietà sopra e le inoltra alla pagina limitando
   `time-pos` a ~4 aggiornamenti/s. Errore di libmpv (assente, non
   caricabile) → eccezione chiara, che fa scegliere il motore browser.
2. **`mornye/api_mixins/native_player.py`** (nuovo):
   `native_player_status`, `native_player_load`, `native_player_command`,
   `native_player_devices`, `native_player_set_options`. Registrare in
   `MornyeAPI` (`mornye/app.py`), `ALLOWED_METHODS`, `web-shim.js`.
   Ogni percorso passa da `_is_approved_path`, come `prepare_playback`.
3. **Adattatore `playerEngine` in `app.js`**: stessa interfaccia minima
   dell'`<audio>` usata dal codice (`play()`, `pause()`, `currentTime`
   get/set, `duration`, `volume`, `muted`, `paused`, `src`, eventi `play`,
   `pause`, `timeupdate`, `ended`, `loadedmetadata`, `seeked`, `error`).
   - Motore browser: delega all'elemento `<audio>` com'è oggi.
   - Motore mpv: inoltra i comandi all'API e genera gli stessi eventi dagli
     aggiornamenti del backend (una funzione `window.app_native_player_event`
     chiamata via `evaluate_js`).
   - I 63 punti passano dall'adattatore, così coda, testi sincronizzati e
     Now Playing non vanno riscritti.
4. **Impostazione "Motore di riproduzione"**: `auto` (default: nativo nel
   desktop se libmpv c'è, altrimenti browser) / `browser` / `native`.
   **In `--web` sempre browser**: l'audio deve uscire dal dispositivo di chi
   ascolta, non dal server.
5. **Niente copia FLAC con mpv**: in `loadPlayerTrack` il motore nativo
   riceve il percorso originale (mpv decodifica tutto ciò che decodifica
   ffmpeg). Limite noto: **AC-4** (ffmpeg non ha il decoder).
6. **Media keys**: con il motore nativo si lascia `media-controls=yes` a mpv
   e si **disattivano** gli handler della Media Session del browser
   (`setActionHandler(…, null)`), per non avere comandi doppi.

## Fasi

### Fase 0 — adattatore senza cambiare comportamento

- Introdurre `playerEngine` con il solo motore browser e ripuntare i 63
  riferimenti. Nessun cambiamento visibile: è il passo che limita il rischio
  del lavoro su `app.js`.
- Test: contratto JS dell'adattatore (eventi e proprietà come `<audio>`);
  tutta la suite del frontend deve restare verde.

### Fase 1 — motore mpv e funzioni per l'impianto

- `native_player.py`, API, motore mpv nell'adattatore, impostazione del
  motore con fallback automatico al browser.
- Impostazioni del player (nuova sezione):
  - **Dispositivo di uscita**: menu da `audio-device-list`.
  - **Bitstream**: interruttori AC-3, E-AC-3, DTS, DTS-HD, TrueHD →
    `audio-spdif`. Nota nell'interfaccia: su S/PDIF (coassiale/ottico) solo
    AC-3 e DTS; E-AC-3/TrueHD/DTS-HD richiedono HDMI.
  - **Modalità esclusiva** → `audio-exclusive`.
- Etichetta di qualità con ciò che esce davvero: "Bitstream AC-3 5.1"
  quando `audio-out-params` è spdif, altrimenti "PCM 24-bit / 96 kHz 5.1".
- `python-mpv` come extra opzionale in `pyproject.toml` (es. `[player]`);
  documentare l'installazione di libmpv in `docs/guide/` e nel README.
- Test:
  - unità con un finto modulo `mpv` (comandi, eventi, throttling, fallback
    quando l'import fallisce);
  - integrazione con il vero libmpv quando presente
    (`pytest.mark.skipif`), `ao=null`, file generati con ffmpeg: durata,
    fine traccia, seek, coda;
  - prova manuale su Windows con la Xonar (bitstream AC-3 sul coassiale →
    l'impianto deve mostrare Dolby Digital 5.1) e via HDMI → TV → ottico.

### Fase 2 — riproduzione

- **Gapless**: `gapless-audio=yes` e precaricamento della traccia
  successiva della coda (`loadfile <path> append`), mantenendo
  `playerOrder` come fonte di verità.
- **ReplayGain**: spento / traccia / album, preamp, clip.
- **Ritardo audio** (ms), **velocità** con correzione dell'intonazione,
  **ripetizione A-B**.

### Fase 3 — equalizzatore e mixaggio

- Equalizzatore a 10 bande con preset (filtro `superequalizer` o catena di
  `equalizer`).
- **Canali di uscita**: auto / stereo / 5.1 / 7.1 (`audio-channels`), con
  downmix (7.1 → 5.1 → stereo, normalizzato) e upmix opzionale.
- Normalizzazione dinamica (`dynaudnorm` / `loudnorm`), spenta di default.
- Nessun filtro attivo quando il bitstream è in uso (non si applicano a un
  flusso compresso): l'interfaccia lo deve dire.

### Fase 4 — rifiniture

- Pannello "Statistiche": codec, canali, formato di uscita, dispositivo,
  buffer (`demuxer-cache-*`).
- Scorciatoie in stile MPC-HC: spazio, frecce (seek), +/- volume, `[`/`]`
  velocità, `M` muto.

## Rischi e cose da verificare

- **Windows reale**: bitstream ed esclusiva vanno provati sul PC con la
  Xonar (192.168.1.72, lo stesso usato nella diagnosi del 5.1).
- **Distribuzione di libmpv su Windows**: fornire la DLL con Mornye o
  chiederne l'installazione.
- **Doppi comandi multimediali** tra `media-controls` di mpv e la Media
  Session della WebView: verificarlo su entrambi i sistemi.
- **Ampiezza della modifica in `app.js`**: per questo la Fase 0 viene prima
  e da sola.
- **Thread**: gli eventi di python-mpv arrivano su un thread suo;
  `evaluate_js` va chiamato senza bloccarlo (coda + invio), come gli altri
  eventi verso la pagina.

## Come riprendere

1. Leggere questo file e verificare che i punti di aggancio indicati siano
   ancora lì (i numeri di riga cambiano).
2. Partire dalla Fase 0, con un commit per fase.
3. Aggiornare la tabella **Stato** a ogni fase chiusa.
