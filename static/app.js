'use strict';
const $ = id => document.getElementById(id);
const defaults = {
  youtube: 'https://www.youtube.com/watch?v=mukiMaOSLEs&list=PL3817D41C7D841E23&index=2',
  instagram: '', tiktok: '', spotify: ''
};
const example2 = 'https://www.youtube.com/watch?v=aLf9lfbI5Kg';
const state = {media_format: 'mp3', platform: 'youtube', mode: 'videos', source: 'link', authorized: false, busy: false, diagnosticBusy: false, timer: null, drafts: {...defaults}};
const platformNames = {youtube:'YouTube',instagram:'Instagram',tiktok:'TikTok',spotify:'Spotify'};
function message(text, id = 'notice', success = false) { const n=$(id); n.textContent=text;n.classList.remove('hidden');n.classList.toggle('success',success); }
function clear(id='notice') { $(id).classList.add('hidden'); }
function authorized(flag){ state.authorized=flag; $('loginCard').classList.toggle('hidden',flag);$('logoutBtn').classList.toggle('hidden',!flag);render(); }
function render(){
  const uploadMode=state.source==='upload' || state.platform==='spotify';
  $('formatMp3').classList.toggle('selected',state.media_format==='mp3');
  $('formatMp4').classList.toggle('selected',state.media_format==='mp4');
  $('formatMp3').setAttribute('aria-pressed',String(state.media_format==='mp3'));
  $('formatMp4').setAttribute('aria-pressed',String(state.media_format==='mp4'));
  for (const button of document.querySelectorAll('[data-platform]')){
    const selected=button.dataset.platform===state.platform;
    button.classList.toggle('selected', selected);
    button.setAttribute('aria-pressed',String(selected));
    button.disabled=state.busy;
  }
  $('formatMp3').disabled=state.busy;$('formatMp4').disabled=state.busy;
  $('videoMode').disabled=state.busy;
  $('playlistMode').disabled=state.busy || state.platform!=='youtube' || uploadMode;
  $('modeRow').classList.toggle('hidden',uploadMode);
  $('videoMode').classList.toggle('selected',state.mode==='videos');
  $('playlistMode').classList.toggle('selected',state.mode==='playlist');
  $('videoMode').setAttribute('aria-pressed',String(state.mode==='videos'));
  $('playlistMode').setAttribute('aria-pressed',String(state.mode==='playlist'));
  $('videoDescription').textContent='Em '+state.media_format.toUpperCase();
  $('sourceSwitch').classList.toggle('hidden',state.platform==='spotify');
  $('sourceLink').classList.toggle('selected',!uploadMode);
  $('sourceUpload').classList.toggle('selected',uploadMode);
  $('sourceLink').setAttribute('aria-pressed',String(!uploadMode));
  $('sourceUpload').setAttribute('aria-pressed',String(uploadMode));
  $('sourceLink').disabled=state.busy;
  $('sourceUpload').disabled=state.busy;
  $('youtubeServerNote').classList.toggle('hidden',state.platform!=='youtube' || uploadMode);
  $('youtubeDiagnostic').classList.toggle('hidden',state.platform!=='youtube' || uploadMode);
  $('diagnosticBtn').disabled=state.busy || state.diagnosticBusy;
  $('spotifyNote').classList.toggle('hidden',state.platform!=='spotify');
  $('urlPanel').classList.toggle('hidden',uploadMode);
  $('uploadPanel').classList.toggle('hidden',!uploadMode);
  $('mp3Quality').classList.toggle('hidden',state.media_format!=='mp3');
  $('mp4Quality').classList.toggle('hidden',state.media_format!=='mp4');
  $('platformName').textContent=platformNames[state.platform];
  $('links').placeholder=({youtube:'https://www.youtube.com/watch?v=...',instagram:'https://www.instagram.com/reel/.../',tiktok:'https://www.tiktok.com/@usuario/video/...'}[state.platform]||'');
  $('linkType').textContent=state.mode==='playlist'?'PLAYLIST':'VÍDEO';
  $('examples').classList.toggle('hidden',state.platform!=='youtube');
  $('linkHelp').textContent=(state.mode==='playlist' ? 'Apenas playlist do YouTube, até '+(state.media_format==='mp3'?20:8)+' itens, dentro de ZIP.' : 'Vídeo público de sua autoria ou com autorização. Instagram/TikTok podem exigir login, que este site não fornece.') + (state.platform==='youtube' ? ' Atenção: o YouTube pode exigir verificação do servidor e bloquear a solicitação.' : '');
  $('convertBtn').textContent=(!state.authorized?'🔒 Entrar para continuar':uploadMode?'↑ Converter meu arquivo em '+state.media_format.toUpperCase():state.mode==='playlist'?'↓ Baixar playlist em ZIP':'↓ Baixar '+state.media_format.toUpperCase());
  $('convertBtn').disabled=state.busy || state.diagnosticBusy;
}
function choosePlatform(platform){
  if (state.busy || state.platform===platform)return;
  state.drafts[state.platform]=$('links').value;
  state.platform=platform;
  state.mode='videos';
  state.source=platform==='spotify'?'upload':'link';
  $('links').value=state.drafts[platform]||'';
  $('progressPanel').classList.add('hidden');$('diagnosticResult').classList.add('hidden');clear();render();
}
function chooseFormat(format){if(state.busy)return;state.media_format=format;$('progressPanel').classList.add('hidden');clear();render();}
function chooseMode(mode){if(state.busy || state.source==='upload' || (mode==='playlist' && state.platform!=='youtube'))return;state.mode=mode;$('progressPanel').classList.add('hidden');clear();render();}
function chooseSource(source){if(state.busy || state.platform==='spotify' || !['link','upload'].includes(source))return;state.source=source;state.mode='videos';$('progressPanel').classList.add('hidden');$('downloadBtn').classList.add('hidden');clear();render();}
async function api(path,options={}){
  const headers = options.body instanceof FormData ? {} : {'Content-Type':'application/json'};
  const response=await fetch(path,{credentials:'same-origin',headers,...options});
  let result={};try{result=await response.json();}catch(_){}
  if (!response.ok) throw Error(result.error||`Erro HTTP ${response.status}`);
  return result;
}
async function init(){try{const r=await api('/api/me');authorized(r.authenticated);if(!r.configured)message('A senha de acesso precisa ser definida no painel do Render.','loginNotice');}catch(e){message('Servidor indisponível: '+e.message,'loginNotice');}}
function busy(flag){state.busy=flag;render();}
async function poll(id){
  try{
    const r=await api('/api/jobs/'+encodeURIComponent(id));const job=r.job;
    $('progressFill').style.width=job.progress+'%';$('progressPercent').textContent=job.progress+'%';
    $('statusText').textContent=job.message;
    $('statusTitle').textContent=job.status==='done'?'Pronto!':job.status==='error'?'Não foi possível concluir':'Processando arquivo...';
    if(job.status==='done'){
      $('downloadBtn').href='/api/jobs/'+encodeURIComponent(id)+'/download';
      $('downloadBtn').textContent=job.filename.endsWith('.zip')?'↓ Baixar ZIP organizado':'↓ Baixar '+job.media_format.toUpperCase();
      $('downloadBtn').classList.remove('hidden');
      message('Pronto: '+job.output_count+' arquivo(s). Disponível por até 30 minutos.','notice',true);busy(false);return;
    }
    if(job.status==='error'){message(job.error||'Não foi possível processar esta mídia.');busy(false);return;}
    state.timer=setTimeout(()=>poll(id),2000);
  }catch(e){message(e.message);busy(false);}
}
$('loginForm').addEventListener('submit',async e=>{
  e.preventDefault();clear('loginNotice');
  try{await api('/api/login',{method:'POST',body:JSON.stringify({password:$('sitePassword').value})});$('sitePassword').value='';authorized(true);}
  catch(e){message(e.message,'loginNotice');}
});
$('logoutBtn').addEventListener('click',async()=>{if(state.timer)clearTimeout(state.timer);try{await api('/api/logout',{method:'POST'});}catch(_){}authorized(false);});
$('formatMp3').addEventListener('click',()=>chooseFormat('mp3'));
$('formatMp4').addEventListener('click',()=>chooseFormat('mp4'));
for(const button of document.querySelectorAll('[data-platform]'))button.addEventListener('click',()=>choosePlatform(button.dataset.platform));
$('videoMode').addEventListener('click',()=>chooseMode('videos'));
$('playlistMode').addEventListener('click',()=>chooseMode('playlist'));
$('sourceLink').addEventListener('click',()=>chooseSource('link'));
$('sourceUpload').addEventListener('click',()=>chooseSource('upload'));
$('example1').addEventListener('click',()=>{$('links').value=defaults.youtube;});
$('example2').addEventListener('click',()=>{$('links').value=example2;});
$('links').addEventListener('input',()=>{state.drafts[state.platform]=$('links').value;$('diagnosticResult').classList.add('hidden');});
$('diagnosticBtn').addEventListener('click',async()=>{
  if(state.busy || state.diagnosticBusy || state.platform!=='youtube' || state.source!=='link')return;
  if(!state.authorized){$('loginCard').scrollIntoView({behavior:'smooth',block:'center'});return;}
  if(!$('permission').checked)return message('Confirme que tem autorização para consultar este vídeo.');
  if(!$('links').value.trim())return message('Cole um link válido do YouTube.');
  state.diagnosticBusy=true;render();
  const target=$('diagnosticResult');
  target.classList.remove('hidden');
  target.textContent='Consultando metadados (sem baixar arquivos)...';
  try{
    const result=await api('/api/diagnose',{method:'POST',body:JSON.stringify({url:$('links').value.trim(),permission:true})});
    const codes={verification_required:'Verificação exigida',rate_limited:'Limite de requisições',forbidden:'Acesso recusado',extractor_signature:'Falha no extrator',private_or_unavailable:'Vídeo indisponível',network_error:'Problema de rede',no_formats:'Sem formatos',ok:'Metadados disponíveis',unknown:'Causa não identificada'};
    const label=codes[result.code]||'Diagnóstico';
    target.textContent=label+': '+result.message+' (yt-dlp '+result.yt_dlp_version+')';
  }catch(e){target.textContent='Não foi possível concluir o diagnóstico: '+e.message;}
  finally{state.diagnosticBusy=false;render();}
});
$('convertBtn').addEventListener('click',async()=>{
  if(state.busy)return;
  if(!state.authorized){$('loginCard').scrollIntoView({behavior:'smooth',block:'center'});return;}
  clear();
  if(!$('permission').checked)return message('Confirme que você tem permissão para obter a mídia.');
  const uploadMode=state.source==='upload' || state.platform==='spotify';
  if(!uploadMode && !$('links').value.trim())return message('Cole o link da plataforma.');
  if(uploadMode && !$('uploadFile').files.length)return message('Selecione um arquivo próprio para converter.');
  busy(true);$('progressPanel').classList.remove('hidden');$('downloadBtn').classList.add('hidden');
  $('progressFill').style.width='0%';$('progressPercent').textContent='0%';$('statusText').textContent='Enviando tarefa...';
  try{
    const quality=document.querySelector('input[name="'+(state.media_format==='mp3'?'mp3Quality':'mp4Quality')+'"]:checked').value;
    let result;
    if(uploadMode){
      const form = new FormData();form.append('file',$('uploadFile').files[0]);form.append('permission','true');form.append('media_format',state.media_format);form.append('quality',quality);
      result=await api('/api/upload',{method:'POST',body:form});
    }else{
      result=await api('/api/jobs',{method:'POST',body:JSON.stringify({links:[$('links').value.trim()],permission:true,mode:state.mode,platform:state.platform,media_format:state.media_format,quality})});
    }
    await poll(result.job.id);
  }catch(e){message(e.message);$('progressPanel').classList.add('hidden');busy(false);}
});
$('links').value=defaults.youtube;
render();init();
