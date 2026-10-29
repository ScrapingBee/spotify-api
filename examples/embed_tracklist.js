// Spotify embed track list as TSV, Node 18+ (global fetch), no dependencies.
//
//   SCRAPINGBEE_API_KEY=... node embed_tracklist.js spotify:playlist:37i9dQZF1DX0D996ZXujBy
//
// One classic request with render_js=false, 1 credit. The embed page is a
// Next.js route, so the track list is already in __NEXT_DATA__.
// Verified live on 2026-10-29.

const API = 'https://app.scrapingbee.com/api/v1/';
const KEY = process.env.SCRAPINGBEE_API_KEY;

function parseRef(ref) {
  const m = ref.match(/(?:spotify:|open\.spotify\.com\/(?:embed\/)?)(artist|album|track|playlist)[:/]([A-Za-z0-9]{22})/);
  if (!m) throw new Error(`not a Spotify reference: ${ref}`);
  return { kind: m[1], id: m[2] };
}

async function scrape(url, attempts = 3) {
  const qs = new URLSearchParams({ url, render_js: 'false' });
  for (let i = 1; i <= attempts; i++) {
    const res = await fetch(`${API}?${qs}`, { headers: { Authorization: `Bearer ${KEY}` } });
    // 500 means the target failed on ScrapingBee's side; it is not billed.
    if (res.status === 500 && i < attempts) {
      await new Promise((r) => setTimeout(r, 2000 * i));
      continue;
    }
    if (!res.ok) throw new Error(`ScrapingBee ${res.status}: ${(await res.text()).slice(0, 200)}`);
    return { html: await res.text(), cost: res.headers.get('spb-cost') };
  }
  throw new Error('unreachable');
}

function entityFrom(html) {
  const m = html.match(/<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)<\/script>/);
  if (!m) throw new Error('no __NEXT_DATA__, not an embed page');
  return JSON.parse(m[1]).props.pageProps.state.data.entity;
}

const mmss = (ms) => `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}`;

async function main() {
  if (!KEY) throw new Error('set SCRAPINGBEE_API_KEY');
  const { kind, id } = parseRef(process.argv[2] || 'spotify:playlist:37i9dQZF1DX0D996ZXujBy');
  const { html, cost } = await scrape(`https://open.spotify.com/embed/${kind}/${id}`);
  const e = entityFrom(html);

  // A track embed has no trackList, it is the track itself.
  const rows = e.trackList || [{ uri: e.uri, title: e.name, subtitle: (e.artists || []).map((a) => a.name).join(', '), duration: e.duration, isExplicit: e.isExplicit }];

  console.error(`${e.type} "${e.name}" by ${e.subtitle}: ${rows.length} rows, ${cost} credit(s)`);
  if (rows.length >= 100) console.error('the embed stops at 100 rows; the playlist may be longer');
  console.log(['pos', 'track_id', 'title', 'artists', 'length', 'explicit'].join('\t'));
  rows.forEach((t, i) => {
    // audioPreview.url is in every row. It is skipped here on purpose.
    console.log([i + 1, t.uri.split(':').pop(), t.title, t.subtitle, mmss(t.duration), t.isExplicit ? 'E' : ''].join('\t'));
  });
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
