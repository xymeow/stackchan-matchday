// HTTP control surface: browser control panel, phone pre-match setup page,
// watcher-facing match-setup endpoints, and the plain-text command API.
import { HttpServerService, Response } from 'matchday/http-server-safe'
import {
  HTTP_PORT,
  MOD_NAME,
  MOD_VERSION,
  nowTicks,
  readPreference,
  savePreference,
  state,
} from 'matchday/state'
import { executeJsonAction, helpText, runCommand, statusPayload } from 'matchday/commands'
import { refreshSetupQr } from 'matchday/ui'

// A language explicitly picked on the phone page (persisted preference) wins
// over whatever language the watcher happens to push with its options.
function applyLanguage(language, fromWatcher = false) {
  const value = String(language ?? '')
    .trim()
    .toLowerCase()
  if (value !== 'zh' && value !== 'en') return false
  if (fromWatcher && readPreference('language', '') !== '') return true
  if (state.matchSetup.language !== value) {
    state.matchSetup.language = value
    if (!fromWatcher) savePreference('language', value)
  }
  return true
}

function response(value, status, contentType) {
  const body = ArrayBuffer.fromString(String(value))
  return new Response(body, {
    status,
    headers: {
      'Content-Type': contentType,
      'Content-Length': body.byteLength,
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
    },
  })
}

function text(value, status = 200) {
  return response(value, status, 'text/plain; charset=utf-8')
}

function json(value, status = 200) {
  return response(JSON.stringify(value), status, 'application/json')
}

function html(value, status = 200) {
  return response(value, status, 'text/html; charset=utf-8')
}

async function readJsonOrText(c) {
  const contentType = c.req.header('content-type') ?? ''
  const body = await c.req.text()
  if (contentType.indexOf('application/json') >= 0) {
    return JSON.parse(body)
  }
  return body
}

// ---------------------------------------------------------------------------
// Match setup relay between the phone page and the watcher.

function matchSetupPayload() {
  return {
    ok: true,
    language: state.matchSetup.language,
    options: state.matchSetup.options,
    current: state.matchSetup.current,
    pending: state.matchSetup.pending,
    lastResult: state.matchSetup.lastResult,
  }
}

function syncMatchSetup(payload) {
  const options = Array.isArray(payload?.options) ? payload.options.slice(0, 12) : []
  state.matchSetup.options = options
  state.matchSetup.current = payload?.current && typeof payload.current === 'object' ? payload.current : {}
  applyLanguage(payload?.language ?? state.matchSetup.current?.language, true)
  return { ok: true, text: `ok match setup options ${options.length}\n` }
}

function queueMatchSetup(payload) {
  const eventId = String(payload?.espn_event_id ?? '').trim()
  const eventTicker = String(payload?.event_ticker ?? '')
    .trim()
    .toUpperCase()
  const option = state.matchSetup.options.find(
    (item) =>
      String(item?.event_id ?? '') === eventId &&
      String(item?.kalshi_event_ticker ?? '').toUpperCase() === eventTicker,
  )
  if (!option) {
    return { ok: false, text: 'error selected match is not available\n' }
  }
  const validTeams = [String(option.home?.name ?? ''), String(option.away?.name ?? '')]
  const favoriteTeam = String(payload?.favorite_team ?? '').trim()
  const positionTeam = String(payload?.position_team ?? '').trim()
  const language = String(payload?.language ?? state.matchSetup.language)
    .trim()
    .toLowerCase()
  if ((favoriteTeam && !validTeams.includes(favoriteTeam)) || (positionTeam && !validTeams.includes(positionTeam))) {
    return { ok: false, text: 'error invalid favorite or position team\n' }
  }
  if (language !== 'zh' && language !== 'en') {
    return { ok: false, text: 'error language must be zh or en\n' }
  }
  const pending = {
    request_id: String(payload?.request_id ?? nowTicks()),
    event_ticker: eventTicker,
    espn_event_id: eventId,
    favorite_team: favoriteTeam,
    position_team: positionTeam,
  }
  pending.language = language
  state.matchSetup.pending = pending
  state.matchSetup.lastResult = null
  savePreference('matchSetupPending', JSON.stringify(pending))
  return { ok: true, text: 'ok match setup queued\n' }
}

function acknowledgeMatchSetup(payload) {
  const requestId = String(payload?.request_id ?? '')
  if (state.matchSetup.pending && requestId && String(state.matchSetup.pending.request_id) !== requestId) {
    return { ok: false, text: 'error setup acknowledgement does not match pending request\n' }
  }
  state.matchSetup.pending = null
  state.matchSetup.lastResult = payload && typeof payload === 'object' ? payload : null
  if (payload?.ok) applyLanguage(payload?.language)
  savePreference('matchSetupPending', undefined)
  return { ok: true, text: 'ok match setup acknowledged\n' }
}

// ---------------------------------------------------------------------------
// Pages.

function indexHtml() {
  return `<!doctype html>
<html>
<head>
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Stack-chan Matchday</title>
  <style>
    body{font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;margin:0;background:#fff7fb;color:#171923}
    main{max-width:620px;margin:0 auto;padding:18px}
    h1{font-size:24px;margin:0 0 12px}
    section{background:#fff;border:1px solid #e6c7d6;border-radius:8px;padding:14px;margin:12px 0}
    button,input{box-sizing:border-box;width:100%;font:inherit;border:1px solid #d7a8be;border-radius:8px;padding:10px;background:#fff}
    button{font-weight:700;background:#f7a2c9}
    .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
    .row{display:grid;grid-template-columns:1fr auto;gap:8px}
    label{display:block;font-size:13px;margin:10px 0 4px}
    pre{white-space:pre-wrap;background:#242638;color:#fff;border-radius:8px;padding:12px;min-height:90px}
  </style>
</head>
<body>
<main>
  <h1>Stack-chan Matchday</h1>
  <section><button onclick="location.href='/setup'">赛前设置</button></section>
  <section>
    <div class="grid">
      <button onclick="cmd('face happy')">happy</button>
      <button onclick="cmd('face neutral')">neutral</button>
      <button onclick="cmd('face surprise')">surprise</button>
      <button onclick="cmd('face sad')">sad</button>
      <button onclick="cmd('face angry')">angry</button>
      <button onclick="cmd('face sleep')">sleep</button>
    </div>
    <div class="grid" style="margin-top:8px">
      <button onclick="cmd('idle look on')">idle look</button>
      <button onclick="cmd('idle look off')">look off</button>
      <button onclick="cmd('balloon off')">hide text</button>
    </div>
    <label>Say</label>
    <div class="row"><input id="say" value="大家好"><button onclick="cmd('say '+say.value)">send</button></div>
    <label>Command</label>
    <div class="row"><input id="raw" value="status"><button onclick="cmd(raw.value)">run</button></div>
  </section>
  <section>
    <label>Look X</label><input id="x" type="range" min="-12" max="12" value="0" oninput="look()">
    <label>Look Y</label><input id="y" type="range" min="-8" max="8" value="0" oninput="look()">
  </section>
  <section>
    <div class="grid">
      <button onclick="cmd('diag')">diag</button>
      <button onclick="cmd('screen wake')">wake</button>
      <button onclick="cmd('screen sleep')">sleep</button>
    </div>
    <div class="grid" style="margin-top:8px">
      <button onclick="cmd('power auto on')">auto dim</button>
      <button onclick="cmd('power auto off')">auto off</button>
      <button onclick="cmd('clip favorite-goal')">goal tone</button>
    </div>
    <label>Brightness</label><input id="bright" type="range" min="0" max="100" value="45" oninput="cmd('screen brightness '+bright.value)">
  </section>
  <pre id="out">loading...</pre>
</main>
<script>
async function cmd(command){
  const response=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'text/plain'},body:command});
  out.textContent=await response.text();
}
function look(){cmd('look '+x.value+' '+y.value)}
async function status(){
  const response=await fetch('/api/status');
  out.textContent=JSON.stringify(await response.json(),null,2);
}
status();
</script>
</body>
</html>`
}

function setupPageHtml() {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <title>Stack-chan 赛前设置</title>
  <style>
    :root{--ink:#17202a;--muted:#65717d;--line:#d8dee4;--bg:#f4f6f7;--red:#d62828;--green:#067647}
    *{box-sizing:border-box;letter-spacing:0}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
    main{width:min(680px,100%);margin:0 auto;padding:18px 14px 36px}header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}h1{font-size:21px;margin:0}.status{font-size:12px;color:var(--muted)}
    section{padding:15px 0;border-top:1px solid var(--line)}h2{font-size:15px;margin:0 0 11px}.matches{display:grid;gap:8px}
    button{width:100%;min-height:44px;padding:10px 12px;border:1px solid var(--line);border-radius:6px;background:#fff;color:var(--ink);font:inherit;font-weight:700;text-align:left}.match{display:grid;grid-template-columns:1fr auto;gap:10px}.match time{font-size:12px;color:var(--muted);align-self:center}.match.selected{border-color:#1769aa;box-shadow:0 0 0 1px #1769aa}
    .form{display:none}.form.show{display:block}.versus{font-size:18px;font-weight:750;margin-bottom:12px}.field{display:block;font-size:13px;font-weight:650;margin:12px 0 6px}
    .segment{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid var(--line);border-radius:6px;overflow:hidden;background:#fff}.segment.two{grid-template-columns:repeat(2,minmax(0,1fr))}.segment label{min-width:0}.segment input{position:absolute;width:1px;height:1px;opacity:0}.segment span{display:flex;align-items:center;justify-content:center;min-height:44px;padding:6px 4px;font-size:13px;text-align:center;border-right:1px solid var(--line);overflow-wrap:anywhere}.segment label:last-child span{border-right:0}.segment input:checked+span{background:#17202a;color:#fff}
    button.primary{margin-top:16px;background:var(--red);border-color:var(--red);color:#fff;text-align:center}.message{min-height:22px;margin-top:10px;font-size:13px;color:var(--muted)}.message.ok{color:var(--green)}.message.error{color:#b42318}.empty,.current,.hint{font-size:13px;line-height:1.6;color:var(--muted)}.hint{margin-top:7px}
    @media(max-width:420px){.match{grid-template-columns:1fr}.match time{justify-self:start}}
  </style>
</head>
<body><main>
  <header><h1 id="pageTitle">Stack-chan 赛前设置</h1><div class="status" id="health"></div></header>
  <section><h2 id="langTitle">播报语言 / Language</h2><div class="segment two" id="language"><label><input type="radio" name="language" value="zh" checked><span>中文</span></label><label><input type="radio" name="language" value="en"><span>English</span></label></div><div class="hint" id="langHint"></div></section>
  <section><h2 id="matchesTitle"></h2><div class="matches" id="matches"></div></section>
  <section class="form" id="form"><div class="versus" id="versus"></div><span class="field" id="favLabel"></span><div class="segment" id="favorite"></div><span class="field" id="posLabel"></span><div class="segment" id="position"></div><button class="primary" id="apply"></button><div class="message" id="message"></div></section>
  <section><h2 id="currentTitle"></h2><div class="current" id="current"></div></section>
</main>
<script>
const I18N={
 zh:{docLang:'zh-CN',locale:'zh-CN',pageTitle:'Stack-chan 赛前设置',online:'设备在线',offline:'连接失败',langTitle:'播报语言 / Language',langHint:'页面即时切换；语音和气泡按此语言播报',matchesTitle:'未来比赛',empty:'watcher 暂无开放盘口',favLabel:'支持球队',posLabel:'赛前持仓',neutral:'中立',nopos:'没买',apply:'开始看球',currentTitle:'当前监控',loading:'读取中',none:'尚未配置',fav:'支持',pos:'持仓',posNone:'无',submitted:'已提交，等待 watcher',started:' 已开始监控',failed:'watcher 设置失败',submitFailed:'提交失败'},
 en:{docLang:'en',locale:'en-US',pageTitle:'Stack-chan Match Setup',online:'Device online',offline:'Connection lost',langTitle:'Language / 播报语言',langHint:'The page switches right away; speech and balloons use this language too',matchesTitle:'Upcoming matches',empty:'No open markets from the watcher yet',favLabel:'Your team',posLabel:'Pregame position',neutral:'Neutral',nopos:'No position',apply:'Start watching',currentTitle:'Now monitoring',loading:'Loading',none:'Not configured yet',fav:'team',pos:'position',posNone:'none',submitted:'Submitted, waiting for the watcher',started:' is now being watched',failed:'The watcher failed to apply it',submitFailed:'Submit failed'}};
let lang='zh';
const t=key=>I18N[lang][key];
const state={selected:null,lastResult:'',languageInitialized:false,data:null};
const $=id=>document.getElementById(id);
const localTime=value=>new Intl.DateTimeFormat(t('locale'),{weekday:'short',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(value));
const pick=(leaf,fallback)=>((leaf||{})[lang])||fallback||'';
const teamName=team=>lang==='en'?((team&&team.name)||(team&&team.localized)||''):((team&&team.localized)||(team&&team.name)||'');
const matchLabel=match=>match.home&&match.away?teamName(match.home)+' vs '+teamName(match.away):pick(match.label_i18n,match.label);
function message(text,kind=''){const el=$('message');el.textContent=text;el.className='message '+kind}
function choices(rootId,name,teams,empty){const root=$(rootId);root.textContent='';[...teams,{name:'',localized:empty,en:empty}].forEach((team,index)=>{const label=document.createElement('label');const text=index===teams.length?empty:teamName(team);label.innerHTML='<input type="radio" name="'+name+'" value="'+team.name+'" '+(index===teams.length?'checked':'')+'><span>'+text+'</span>';root.appendChild(label)})}
function choose(match,keepMessage){state.selected=match;document.querySelectorAll('.match').forEach(el=>el.classList.toggle('selected',el.dataset.id===match.event_id));const teams=[match.home,match.away];$('versus').textContent=matchLabel(match);choices('favorite','favorite_team',teams,t('neutral'));choices('position','position_team',teams,t('nopos'));$('form').classList.add('show');if(!keepMessage)message('')}
function currentLine(current){const label=pick(current.label_i18n,current.label);if(!label)return t('none');const fav=pick(current.favorite_team_i18n,current.favorite_team)||t('neutral');const pos=pick(current.position_team_i18n,current.position_team)||t('posNone');return label+' \\u00b7 '+t('fav')+' '+fav+' \\u00b7 '+t('pos')+' '+pos}
function render(data){state.data=data;const root=$('matches');root.textContent='';if(!data.options.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=t('empty');root.appendChild(empty)}
data.options.forEach(match=>{const button=document.createElement('button');button.className='match';button.dataset.id=match.event_id;button.innerHTML='<strong></strong><time></time>';button.querySelector('strong').textContent=matchLabel(match);button.querySelector('time').textContent=localTime(match.starts_at);button.onclick=()=>choose(match);root.appendChild(button)});
const current=data.current||{};if(!state.languageInitialized){state.languageInitialized=true;setLanguage((data.language||current.language)==='en'?'en':'zh',false)}
$('current').textContent=currentLine(current);
if(data.lastResult&&data.lastResult.request_id!==state.lastResult){state.lastResult=data.lastResult.request_id;if(data.lastResult.ok)message((pick(data.lastResult.label_i18n,data.lastResult.label)||'')+t('started'),'ok');else message(data.lastResult.error||t('failed'),'error')}}
function applyStatic(){document.documentElement.lang=t('docLang');document.title=t('pageTitle');$('pageTitle').textContent=t('pageTitle');$('langTitle').textContent=t('langTitle');$('langHint').textContent=t('langHint');$('matchesTitle').textContent=t('matchesTitle');$('favLabel').textContent=t('favLabel');$('posLabel').textContent=t('posLabel');$('apply').textContent=t('apply');$('currentTitle').textContent=t('currentTitle');if(!state.data)$('current').textContent=t('loading')}
function setLanguage(next,post){lang=next==='en'?'en':'zh';const input=document.querySelector('input[name="language"][value="'+lang+'"]');if(input)input.checked=true;applyStatic();if(state.data)render(state.data);if(state.selected)choose(state.selected,true);if(post)fetch('/api/match-setup/language',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({language:lang})}).catch(()=>{})}
async function refresh(){try{const response=await fetch('/api/match-setup');render(await response.json());$('health').textContent=t('online')}catch(error){$('health').textContent=t('offline')}finally{setTimeout(refresh,3000)}}
$('apply').onclick=async()=>{if(!state.selected)return;const favorite=document.querySelector('input[name="favorite_team"]:checked')?.value||'';const position=document.querySelector('input[name="position_team"]:checked')?.value||'';const payload={request_id:String(Date.now()),event_ticker:state.selected.kalshi_event_ticker,espn_event_id:state.selected.event_id,favorite_team:favorite,position_team:position,language:lang};message(t('submitted'));try{const response=await fetch('/api/match-setup/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const result=await response.json();if(!response.ok)throw new Error(result.error||t('submitFailed'))}catch(error){message(error.message,'error')}};
document.querySelectorAll('input[name="language"]').forEach(input=>{input.onchange=()=>setLanguage(input.value,true)});
applyStatic();
refresh();
</script></body></html>`
}

// ---------------------------------------------------------------------------

export function startHttp(robot) {
  const server = new HttpServerService({ port: HTTP_PORT })

  server.get('/', () => html(indexHtml()))
  server.get('/setup', () => html(setupPageHtml()))
  server.get('/health', () => json({ ok: true, mod: MOD_NAME, version: MOD_VERSION }))
  server.get('/api/status', () => json(statusPayload()))
  server.get('/api/help', () => text(helpText()))
  server.get('/api/match-setup', () => json(matchSetupPayload()))
  server.get('/api/match-setup/pending', () =>
    json({ pending: state.matchSetup.pending, language: state.matchSetup.language }),
  )

  server.post('/api/match-setup/options', async (c) => {
    const payload = await readJsonOrText(c)
    const result = typeof payload === 'object' ? syncMatchSetup(payload) : { ok: false, text: 'error JSON required\n' }
    if (result.ok) refreshSetupQr(robot)
    return json({ ok: result.ok, text: result.text }, result.ok ? 200 : 400)
  })

  server.post('/api/match-setup/language', async (c) => {
    const payload = await readJsonOrText(c)
    const language = typeof payload === 'object' ? payload?.language : payload
    if (!applyLanguage(language)) {
      return json({ ok: false, error: 'language must be zh or en' }, 400)
    }
    refreshSetupQr(robot)
    return json({ ok: true, language: state.matchSetup.language })
  })

  server.post('/api/match-setup/apply', async (c) => {
    const payload = await readJsonOrText(c)
    const result = typeof payload === 'object' ? queueMatchSetup(payload) : { ok: false, text: 'error JSON required\n' }
    return json({ ok: result.ok, text: result.text, error: result.ok ? undefined : result.text.trim() }, result.ok ? 200 : 400)
  })

  server.post('/api/match-setup/ack', async (c) => {
    const payload = await readJsonOrText(c)
    const result = typeof payload === 'object' ? acknowledgeMatchSetup(payload) : { ok: false, text: 'error JSON required\n' }
    return json({ ok: result.ok, text: result.text }, result.ok ? 200 : 400)
  })

  server.post('/api/command', async (c) => {
    const body = await c.req.text()
    const result = await runCommand(robot, body)
    return text(result.text, result.ok ? 200 : 400)
  })

  server.post('/api/control', async (c) => {
    const payload = await readJsonOrText(c)
    const result =
      typeof payload === 'string' ? await runCommand(robot, payload) : await executeJsonAction(robot, payload)
    if (result.ok) {
      return json({ ok: true, text: result.text, status: statusPayload() })
    }
    return json({ ok: false, text: result.text, status: statusPayload() }, 400)
  })

  trace(`[matchday] HTTP listening on port ${HTTP_PORT}\n`)
  return server
}
