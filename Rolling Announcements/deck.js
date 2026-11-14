// Shared code for announcements.html (the OBS display) and index.html (the link builder).
// Needs pdf.js 3.11.174 loaded first.
(function () {
  'use strict';

  const PDFJS = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/';

  const DEFAULTS = {
    deck: '1AH8ZmhZ2Of6iqCayA1F1kqcPL7g67rN6TjlR2UjpYJY',
    defaultSeconds: 5,
    fadeMs: 600,
    background: '#000000',   // or "transparent"
    timings: {},             // slide fingerprint -> seconds
  };

  // pdf.js worker lives on a CDN; wrap it in a same-origin blob so it can start.
  let workerReady = false;
  function initPdf() {
    if (workerReady || !window.pdfjsLib) return !!window.pdfjsLib;
    const blob = new Blob([`importScripts(${JSON.stringify(PDFJS + 'pdf.worker.min.js')});`], { type: 'text/javascript' });
    pdfjsLib.GlobalWorkerOptions.workerPort = new Worker(URL.createObjectURL(blob));
    workerReady = true;
    return true;
  }

  // Accepts a full Google Slides link or a bare deck ID.
  function deckId(s) {
    s = String(s || '').trim();
    const m = s.match(/\/presentation\/d\/([\w-]+)/) || s.match(/\/d\/([\w-]+)/);
    if (m) return m[1];
    return /^[\w-]{20,}$/.test(s) ? s : '';
  }

  // ---------- settings <-> page URL ----------
  // Everything lives in the query string, so the OBS Browser Source URL *is* the configuration:
  //   ?deck=<id>&default=5&fade=600&bg=000000&t=<slide>:<seconds>,<slide>:<seconds>
  function fromQuery(search) {
    const q = new URLSearchParams(search);
    const c = Object.assign({}, DEFAULTS, { timings: {} });
    if (q.has('deck') || q.has('id')) c.deck = deckId(q.get('deck') || q.get('id')) || '';
    const d = parseFloat(q.get('default'));
    if (d > 0) c.defaultSeconds = Math.max(0.5, d);
    if (q.has('fade')) { const f = parseInt(q.get('fade'), 10); if (f >= 0) c.fadeMs = f; }
    const bg = q.get('bg');
    if (bg) c.background = /^[0-9a-f]{3,8}$/i.test(bg) ? '#' + bg : bg;
    for (const pair of (q.get('t') || '').split(',')) {
      const [fp, secs] = pair.split(':');
      if (fp && parseFloat(secs) > 0) c.timings[fp] = Math.max(0.5, parseFloat(secs));
    }
    return c;
  }

  function toQuery(c) {
    const q = new URLSearchParams();
    q.set('deck', deckId(c.deck));
    if (c.defaultSeconds !== DEFAULTS.defaultSeconds) q.set('default', c.defaultSeconds);
    if (c.fadeMs !== DEFAULTS.fadeMs) q.set('fade', c.fadeMs);
    if (c.background !== DEFAULTS.background) q.set('bg', String(c.background).replace(/^#/, ''));
    const t = Object.entries(c.timings).filter(([, s]) => s > 0).map(([fp, s]) => `${fp}:${s}`).join(',');
    if (t) q.set('t', t);
    return q.toString().replace(/%3A/gi, ':').replace(/%2C/gi, ',');
  }

  // ---------- minimal .pptx (zip) reader ----------
  function openZip(buf) {
    const dv = new DataView(buf);
    let e = buf.byteLength - 22;
    while (e >= 0 && dv.getUint32(e, true) !== 0x06054b50) e--;
    if (e < 0) throw new Error('not a zip file');
    const count = dv.getUint16(e + 10, true);
    let p = dv.getUint32(e + 16, true);
    const td = new TextDecoder();
    const entries = {};
    for (let k = 0; k < count; k++) {
      const nameLen = dv.getUint16(p + 28, true);
      entries[td.decode(new Uint8Array(buf, p + 46, nameLen))] = {
        method: dv.getUint16(p + 10, true),
        csize: dv.getUint32(p + 20, true),
        size: dv.getUint32(p + 24, true),
        offset: dv.getUint32(p + 42, true),
      };
      p += 46 + nameLen + dv.getUint16(p + 30, true) + dv.getUint16(p + 32, true);
    }
    async function readText(name) {
      const ent = entries[name];
      if (!ent) return null;
      const start = ent.offset + 30 + dv.getUint16(ent.offset + 26, true) + dv.getUint16(ent.offset + 28, true);
      const raw = new Uint8Array(buf, start, ent.csize);
      if (ent.method === 0) return td.decode(raw);
      const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
      return new Response(stream).text();
    }
    return { readText, size: (name) => (entries[name] ? entries[name].size : 0) };
  }

  const xml = (s) => new DOMParser().parseFromString(s || '<x/>', 'application/xml');
  const tags = (doc, name) => Array.from(doc.getElementsByTagNameNS('*', name));

  function resolvePath(fromFile, target) {
    if (target.startsWith('/')) return target.slice(1);
    const parts = fromFile.split('/').slice(0, -1);
    for (const seg of target.split('/')) {
      if (seg === '..') parts.pop(); else if (seg !== '.') parts.push(seg);
    }
    return parts.join('/');
  }

  async function rels(zip, file) {
    const i = file.lastIndexOf('/');
    const txt = await zip.readText(`${file.slice(0, i)}/_rels/${file.slice(i + 1)}.rels`);
    if (!txt) return [];
    return tags(xml(txt), 'Relationship')
      .filter((r) => r.getAttribute('TargetMode') !== 'External')
      .map((r) => ({
        id: r.getAttribute('Id'),
        type: r.getAttribute('Type') || '',
        path: resolvePath(file, r.getAttribute('Target') || ''),
      }));
  }

  // Seconds from speaker notes: "12s", "12 sec", "1.5 min", "time: 12", "duration=8" ...
  function secondsFromNotes(text) {
    let m = text.match(/\b(?:time|timing|duration|delay|seconds?|secs?)\s*[:=]\s*(\d+(?:\.\d+)?)\s*(m(?:in(?:ute)?s?)?\b)?/i);
    if (m) return parseFloat(m[1]) * (m[2] ? 60 : 1);
    m = text.match(/(?:^|[^\w.])(\d+(?:\.\d+)?)\s*(s|secs?|seconds?|m|mins?|minutes?)\b/i);
    if (m) return parseFloat(m[1]) * (/^m/i.test(m[2]) ? 60 : 1);
    return null;
  }

  // Short stable hash (cyrb53).
  function hash(str) {
    let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
    for (let i = 0; i < str.length; i++) {
      const ch = str.charCodeAt(i);
      h1 = Math.imul(h1 ^ ch, 2654435761);
      h2 = Math.imul(h2 ^ ch, 1597334677);
    }
    h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
    h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
    return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
  }

  // One entry per slide in deck order: {hidden, notesSeconds, fp}.
  // fp (fingerprint) is built from the slide's text and pictures, so a custom timing
  // stays with its slide when slides are added, removed or reordered.
  async function readSlides(buf) {
    const zip = openZip(buf);
    const pres = 'ppt/presentation.xml';
    const presDoc = xml(await zip.readText(pres));
    const presRels = await rels(zip, pres);
    const out = [];
    const seen = {};
    for (const sld of tags(presDoc, 'sldId')) {
      const rid = sld.getAttribute('r:id') ||
        sld.getAttributeNS('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'id');
      const rel = presRels.find((r) => r.id === rid);
      const info = { hidden: false, notesSeconds: null, fp: '' };
      out.push(info);
      if (!rel) { info.fp = 'slide' + out.length; continue; }
      const doc = xml(await zip.readText(rel.path));
      info.hidden = doc.documentElement.getAttribute('show') === '0';
      const slideRels = await rels(zip, rel.path);

      const notesRel = slideRels.find((r) => r.type.endsWith('/notesSlide'));
      if (notesRel) {
        const nd = xml(await zip.readText(notesRel.path));
        const text = tags(nd, 'sp')
          .filter((sp) => tags(sp, 'ph').some((ph) => ph.getAttribute('type') === 'body'))
          .map((sp) => tags(sp, 'p').map((p) => tags(p, 't').map((t) => t.textContent).join('')).join('\n'))
          .join('\n');
        info.notesSeconds = secondsFromNotes(text);
      }
      if (info.notesSeconds == null) {
        const adv = tags(doc, 'transition').map((t) => t.getAttribute('advTm')).find((v) => v != null);
        if (adv != null && +adv > 0) info.notesSeconds = +adv / 1000;
      }

      const text = tags(doc, 't').map((t) => t.textContent).join(' ').replace(/\s+/g, ' ').trim();
      const media = slideRels
        .filter((r) => /\/(image|media|video|audio)$/.test(r.type))
        .map((r) => zip.size(r.path)).sort((a, b) => a - b).join(',');
      let fp = hash(text + '|' + media).slice(0, 6);
      seen[fp] = (seen[fp] || 0) + 1;
      if (seen[fp] > 1) fp += '-' + seen[fp];   // identical slides: tell them apart by order
      info.fp = fp;
    }
    return out;
  }

  // ---------- deck loading ----------
  async function download(id, kind) {
    const r = await fetch(`https://docs.google.com/presentation/d/${id}/export/${kind}`, { cache: 'no-store' });
    if (!r.ok) throw new Error(`${kind} download failed (HTTP ${r.status}) - is the deck shared "Anyone with the link"?`);
    if ((r.headers.get('content-type') || '').includes('text/html')) throw new Error('deck is not shared publicly');
    return r.arrayBuffer();
  }

  // Returns {pdf, slides: [{n, page, hidden, notesSeconds, fp}]}.
  // n = slide number in the deck, page = PDF page to draw (null if the PDF left it out).
  async function loadDeck(id) {
    if (!initPdf()) throw new Error("couldn't load the PDF renderer (no internet?)");
    const [pdfBuf, pptxBuf] = await Promise.all([download(id, 'pdf'), download(id, 'pptx')]);
    const pdf = await pdfjsLib.getDocument({ data: pdfBuf }).promise;
    let info = [];
    try { info = await readSlides(pptxBuf); }
    catch (err) { console.warn('Could not read slide details, using defaults:', err); }

    const visibleCount = info.filter((s) => !s.hidden).length;
    let slides;
    if (info.length === pdf.numPages) {           // PDF contains every slide
      slides = info.map((s, i) => ({ n: i + 1, page: i + 1, ...s }));
    } else if (info.length && visibleCount === pdf.numPages) {   // PDF already omits skipped slides
      let page = 0;
      slides = info.map((s, i) => ({ n: i + 1, page: s.hidden ? null : ++page, ...s }));
    } else {                                      // can't line them up: show every PDF page
      slides = Array.from({ length: pdf.numPages }, (_, i) =>
        ({ n: i + 1, page: i + 1, hidden: false, notesSeconds: null, fp: 'page' + (i + 1) }));
    }
    return { pdf, slides };
  }

  // How long a slide stays up: custom (settings page) > speaker notes > default.
  function timing(slide, cfg) {
    const custom = +cfg.timings[slide.fp];
    if (custom > 0) return { seconds: custom, source: 'custom' };
    if (slide.notesSeconds > 0) return { seconds: slide.notesSeconds, source: 'notes' };
    return { seconds: cfg.defaultSeconds, source: 'default' };
  }

  // Draws a PDF page into a canvas, fitted inside width x height CSS pixels.
  async function render(pdf, pageNo, canvas, width, height) {
    const page = await pdf.getPage(pageNo);
    const base = page.getViewport({ scale: 1 });
    const dpr = window.devicePixelRatio || 1;
    const scale = Math.max(0.1, Math.min(width / base.width, height / base.height) * dpr);
    const vp = page.getViewport({ scale });
    canvas.width = Math.round(vp.width);
    canvas.height = Math.round(vp.height);
    await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise;
  }

  window.Deck = { DEFAULTS, deckId, fromQuery, toQuery, loadDeck, timing, render };
})();
