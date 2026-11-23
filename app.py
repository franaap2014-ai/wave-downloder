"""Wave Downloder — organizador de mídias próprias/autorizadas. Sem DRM ou login de terceiros."""
from __future__ import annotations

import hmac
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flask import Flask, jsonify, render_template, request, send_file, session
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET') or secrets.token_hex(32)
app.config.update(
    MAX_CONTENT_LENGTH=110 * 1024 * 1024,  # uploads locais; JSON validado abaixo
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('APP_ENV') == 'production',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=6),
)

ACCESS_KEY = os.environ.get('ACCESS_KEY', '')
DATABASE_URL = os.environ.get('DATABASE_URL', '')
BASE = Path(tempfile.gettempdir()) / 'wave_downloder_jobs'
BASE.mkdir(parents=True, exist_ok=True)
MAX_PLAYLIST_MP3 = 20
MAX_PLAYLIST_MP4 = 8
MAX_DURATION = 15 * 60
MAX_SINGLE_BYTES = 85 * 1024 * 1024
MAX_FINAL_BYTES = 250 * 1024 * 1024
JOB_TTL_SECONDS = 30 * 60
VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
PLAYLIST_ID = re.compile(r'^[A-Za-z0-9_-]{8,70}$')
INSTAGRAM_ID = re.compile(r'^[A-Za-z0-9_-]{5,48}$')
TIKTOK_ID = re.compile(r'^[0-9]{9,25}$')
TIKTOK_SHORT = re.compile(r'^[A-Za-z0-9_-]{4,36}$')
UPLOAD_EXTS = {'.mp3', '.m4a', '.aac', '.wav', '.flac', '.ogg', '.mp4', '.mov', '.webm'}

jobs: dict[str, dict] = {}
lock = threading.RLock()
active_job: str | None = None
recent_jobs: deque[float] = deque()
login_attempts: dict[str, deque[float]] = {}
recent_diagnostics: deque[float] = deque()
diagnostic_busy = False

# Ponte local opcional: um unico computador por vez, sem abrir portas no Windows.
bridge_token: str | None = None
bridge_seen_at: float = 0.0
BRIDGE_HEARTBEAT_SECONDS = 60


def safe_name(value: str, fallback: str = 'Mídia') -> str:
    value = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', ' ', str(value))
    value = re.sub(r'\s+', ' ', value).strip(' .')
    return value[:90] or fallback


def normalized_link(raw: str, platform: str = 'youtube', playlist: bool = False) -> str:
    """Only canonical URLs for known public extractors, preventing arbitrary URL fetches."""
    if not isinstance(raw, str) or not (12 <= len(raw) <= 600):
        raise ValueError('Cole um link válido.')
    try:
        parsed = urlparse(raw.strip())
        if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port:
            raise ValueError('Utilize um link HTTPS válido.')
        host = (parsed.hostname or '').lower().rstrip('.')
    except ValueError as exc:
        raise ValueError('Link HTTPS inválido.') from exc
    if not host or parsed.fragment:
        raise ValueError('Link inválido.')
    qs = parse_qs(parsed.query)

    if platform == 'youtube':
        if host not in {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}:
            raise ValueError('Cole um link do YouTube.')
        if playlist:
            pid = (qs.get('list') or [''])[0]
            if not PLAYLIST_ID.fullmatch(pid):
                raise ValueError('Playlist do YouTube inválida (é necessário list=...).')
            return f'https://www.youtube.com/playlist?list={pid}'
        if host == 'youtu.be':
            vid = parsed.path.strip('/')
        elif parsed.path == '/watch':
            vid = (qs.get('v') or [''])[0]
        elif re.fullmatch(r'/(shorts|live)/[A-Za-z0-9_-]{11}/?', parsed.path):
            vid = parsed.path.strip('/').split('/')[-1]
        else:
            vid = ''
        if not VIDEO_ID.fullmatch(vid):
            raise ValueError('Não encontrei um vídeo válido do YouTube.')
        return f'https://www.youtube.com/watch?v={vid}'

    if playlist:
        raise ValueError('Playlists estão disponíveis apenas para YouTube.')
    if platform == 'instagram':
        if host not in {'instagram.com', 'www.instagram.com'}:
            raise ValueError('Cole um link do Instagram.')
        m = re.fullmatch(r'/(reel|p|tv)/([A-Za-z0-9_-]{5,48})/?', parsed.path)
        if not m or not INSTAGRAM_ID.fullmatch(m.group(2)):
            raise ValueError('Cole o link de uma publicação ou Reel público do Instagram.')
        return f'https://www.instagram.com/{m.group(1)}/{m.group(2)}/'

    if platform == 'tiktok':
        if host not in {'tiktok.com', 'www.tiktok.com', 'm.tiktok.com', 'vm.tiktok.com', 'vt.tiktok.com'}:
            raise ValueError('Cole um link do TikTok.')
        if host in {'vm.tiktok.com', 'vt.tiktok.com'}:
            token = parsed.path.strip('/')
            if not TIKTOK_SHORT.fullmatch(token):
                raise ValueError('Link curto do TikTok inválido.')
            return f'https://{host}/{token}/'
        m = re.fullmatch(r'/@([A-Za-z0-9._]{2,32})/video/([0-9]{9,25})/?', parsed.path)
        if not m or not TIKTOK_ID.fullmatch(m.group(2)):
            raise ValueError('Cole o link de um vídeo público do TikTok.')
        return f'https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}'

    if platform == 'spotify':
        raise ValueError('Links do Spotify não podem ser exportados como MP3/MP4. Use um arquivo seu na opção "Arquivo próprio".')
    raise ValueError('Plataforma não reconhecida.')



# Mensagens públicas não expõem URL, cookies, cabeçalhos ou respostas brutas.
DIAGNOSTIC_MESSAGES = {
    'verification_required': 'O YouTube exigiu verificação desta conexão. Não é possível concluir apenas com essa mensagem se o problema é do IP, da sessão ou da política de acesso.',
    'rate_limited': 'O YouTube respondeu com limite de requisições (429). Isso não comprova bloqueio permanente do IP.',
    'forbidden': 'O YouTube recusou o acesso (403). Pode ser restrição do recurso, cliente ou conexão; o código sozinho não identifica a causa.',
    'extractor_signature': 'O extrator relatou dificuldade com a assinatura do vídeo. Verifique a versão do yt-dlp e os avisos de compatibilidade.',
    'private_or_unavailable': 'O vídeo não está disponível publicamente para esta conexão.',
    'network_error': 'Falha de rede ou tempo esgotado durante a consulta ao YouTube.',
    'no_formats': 'Os metadados foram obtidos, mas não há formatos utilizáveis. Pode haver restrições adicionais.',
    'ok': 'Metadados e formatos disponíveis nesta consulta. Isso não garante que o download do arquivo funcionará.',
    'unknown': 'Não foi possível determinar a causa da falha de extração pelos sinais observados.',
}


def classify_youtube_diagnostic(raw: str) -> str:
    """Classifica sinais observáveis sem inferir reputação do ASN ou burlas."""
    msg = str(raw).lower()
    if any(mark in msg for mark in ('sign in to confirm', 'not a bot', 'login_required', 'confirm you’re not a bot')):
        return 'verification_required'
    if any(mark in msg for mark in ('http error 429', 'status code 429', 'too many requests')):
        return 'rate_limited'
    if any(mark in msg for mark in ('signature extraction failed', 'nsig extraction failed', 'signature decipher')):
        return 'extractor_signature'
    if any(mark in msg for mark in ('http error 403', 'status code 403', '403 forbidden')):
        return 'forbidden'
    if any(mark in msg for mark in ('private video', 'video unavailable', 'this video is unavailable', 'video is not available')):
        return 'private_or_unavailable'
    if any(mark in msg for mark in ('timed out', 'timeout', 'connection reset', 'network is unreachable', 'name resolution')):
        return 'network_error'
    return 'unknown'


class _PrivateYtdlpLogger:
    """Recebe a saída verbose, mas não a grava nem a publica: somente a categoria."""
    def __init__(self):
        self.lines = []

    def _collect(self, message):
        if len(self.lines) < 120:
            self.lines.append(str(message)[:300])

    def debug(self, message):
        self._collect(message)

    def warning(self, message):
        self._collect(message)

    def error(self, message):
        self._collect(message)


def log_job(job: dict) -> None:
    """Metadata only: never store media, page URLs, passwords, IPs or cookies in Neon."""
    if not DATABASE_URL:
        return
    try:
        import psycopg
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute('''INSERT INTO download_jobs
                     (job_id, mode, status, source_count, output_count, finished_at,
                      error_message, platform, media_format)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                     ON CONFLICT (job_id) DO UPDATE SET
                       status=EXCLUDED.status, output_count=EXCLUDED.output_count,
                       finished_at=EXCLUDED.finished_at, error_message=EXCLUDED.error_message''',
                    (job['id'], job['mode'], job['status'], job['source_count'],
                     job['output_count'], job.get('finished_at'), job.get('error'),
                     job['platform'], job['media_format']))
    except Exception as exc:
        app.logger.warning('Histórico do Neon indisponível (%s)', type(exc).__name__)


def prune() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with lock:
        for jid, job in list(jobs.items()):
            if job['created_ts'] < cutoff and job['status'] in {'done', 'error'}:
                shutil.rmtree(job['directory'], ignore_errors=True)
                del jobs[jid]
        while recent_jobs and recent_jobs[0] < time.time() - 3600:
            recent_jobs.popleft()


def requires_auth(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get('authorized'):
            return jsonify(error='Faça login para acessar a conversão.'), 401
        return fn(*args, **kwargs)
    return wrapper


@app.post('/api/diagnose')
@requires_auth
def diagnose_youtube():
    """Sonda metadados de UM vídeo público por vez; não baixa nem muda de cliente."""
    global diagnostic_busy
    if request.content_length and request.content_length > 12000:
        return jsonify(error='Pedido muito grande.'), 413
    data = request.get_json(silent=True) or {}
    if data.get('permission') is not True:
        return jsonify(error='Confirme a autorização para consultar o vídeo.'), 400
    try:
        link = normalized_link(data.get('url'), 'youtube', False)
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400
    now = time.time()
    with lock:
        while recent_diagnostics and recent_diagnostics[0] < now - 3600:
            recent_diagnostics.popleft()
        if diagnostic_busy or (active_job and jobs.get(active_job, {}).get('status') in {'queued', 'running'}):
            return jsonify(error='O servidor está ocupado. Tente novamente quando concluir.'), 429
        if len(recent_diagnostics) >= 3:
            return jsonify(error='Limite de três diagnósticos por hora atingido.'), 429
        recent_diagnostics.append(now)
        diagnostic_busy = True
    collector = _PrivateYtdlpLogger()
    code = 'unknown'
    version = 'desconhecida'
    try:
        import yt_dlp
        version = str(yt_dlp.version.__version__)
        opts = {
            'logger': collector,
            'verbose': True,
            'quiet': True,
            'no_warnings': False,
            'simulate': True,
            'skip_download': True,
            'noplaylist': True,
            'socket_timeout': 10,
            'retries': 0,
            'extractor_retries': 0,
            'fragment_retries': 0,
        }
        with yt_dlp.YoutubeDL(opts) as downloader:
            info = downloader.extract_info(link, download=False)
        code = 'ok' if isinstance(info, dict) and info.get('formats') else 'no_formats'
    except Exception as exc:
        code = classify_youtube_diagnostic(str(exc) + '\n' + '\n'.join(collector.lines))
    finally:
        with lock:
            diagnostic_busy = False
    app.logger.info('Diagnóstico YouTube: categoria=%s; versão yt-dlp=%s', code, version)
    return jsonify(code=code, message=DIAGNOSTIC_MESSAGES[code], yt_dlp_version=version)


@app.get('/')
def index():
    return render_template('index.html')


@app.get('/health')
def health():
    # Health check only tests server readiness; availability of a platform is not guaranteed.
    return jsonify(ok=True, ffmpeg=shutil.which('ffmpeg') is not None, deno=shutil.which('deno') is not None)


@app.get('/api/me')
def me():
    return jsonify(authenticated=bool(session.get('authorized')), configured=bool(ACCESS_KEY))



def _bridge_authorized() -> bool:
    """O token de pareamento nunca aparece em logs e somente o agente o recebe."""
    supplied = request.headers.get('X-Wave-Bridge', '')
    with lock:
        saved = bridge_token
    return bool(saved and supplied and hmac.compare_digest(supplied, saved))


@app.post('/api/bridge/pair')
@requires_auth
def bridge_pair():
    """Um novo pareamento substitui o anterior. Sem senha do site no computador."""
    global bridge_token, bridge_seen_at
    with lock:
        bridge_token = secrets.token_urlsafe(32)
        bridge_seen_at = 0.0
        token = bridge_token
    return jsonify(token=token, message='Cole este codigo no INICIAR_PONTE_WEB.bat do seu Windows.')


@app.get('/api/bridge/status')
@requires_auth
def bridge_status():
    with lock:
        connected = bool(bridge_token and time.time() - bridge_seen_at < BRIDGE_HEARTBEAT_SECONDS)
    return jsonify(connected=connected, mode='local_windows', configured=bool(bridge_token))


@app.post('/api/bridge/next')
def bridge_next():
    global bridge_seen_at
    if not _bridge_authorized():
        return jsonify(error='Pareamento invalido. Gere um novo codigo no site.'), 403
    with lock:
        bridge_seen_at = time.time()
        pending = next((x for x in jobs.values()
                        if x.get('engine') == 'bridge' and x['status'] == 'queued'), None)
        if not pending:
            return ('', 204)
        pending.update(status='running', progress=6, message='Computador conectado. Preparando a midia...')
        payload = {k:pending[k] for k in ('id','mode','media_format','platform','quality')}
        payload['url'] = pending['links'][0]
    return jsonify(job=payload)


@app.post('/api/bridge/progress')
def bridge_progress():
    if not _bridge_authorized():
        return jsonify(error='Nao autorizado.'), 403
    data = request.get_json(silent=True) or {}
    with lock:
        job = jobs.get(str(data.get('id', '')))
        if not job or job.get('engine') != 'bridge' or job['status'] != 'running':
            return jsonify(error='Tarefa indisponivel.'), 404
        try:
            requested = int(data.get('progress', 10))
        except (TypeError, ValueError):
            requested = 10
        job['progress'] = max(job['progress'], min(92, max(6, requested)))
        job['message'] = 'Processando no seu computador...'
    return jsonify(ok=True)


@app.post('/api/bridge/failure')
def bridge_failure():
    global active_job
    if not _bridge_authorized():
        return jsonify(error='Nao autorizado.'), 403
    data = request.get_json(silent=True) or {}
    jid = str(data.get('id', ''))
    with lock:
        job = jobs.get(jid)
        if not job or job.get('engine') != 'bridge' or job['status'] not in {'queued','running'}:
            return jsonify(error='Tarefa nao encontrada.'), 404
        job.update(status='error', error='A conversao falhou no computador. Confira a janela da ponte local.',
                   message='Falha no computador.', finished_at=datetime.now(timezone.utc))
        if active_job == jid:
            active_job = None
    shutil.rmtree(job['directory'], ignore_errors=True)
    log_job(job)
    return jsonify(ok=True)


@app.post('/api/bridge/result')
def bridge_result():
    global active_job
    if not _bridge_authorized():
        return jsonify(error='Nao autorizado.'), 403
    jid = request.form.get('id','')
    upload = request.files.get('file')
    with lock:
        job = jobs.get(jid)
        if not job or job.get('engine') != 'bridge' or job['status'] != 'running':
            return jsonify(error='Tarefa nao encontrada.'), 404
        expected = '.zip' if job['mode']=='playlist' else '.'+job['media_format']
        name = safe_name(Path(upload.filename or '').stem) + expected if upload else ''
        folder = Path(job['directory'])
    if not upload or Path(upload.filename or '').suffix.lower() != expected:
        return jsonify(error='Tipo de arquivo inesperado.'), 400
    output = folder / name
    try:
        size = 0
        with open(output, 'wb') as dest:
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SINGLE_BYTES:
                    raise ValueError('Arquivo acima do limite de 85 MB.')
                dest.write(chunk)
        if size < 1:
            raise ValueError('Arquivo vazio.')
    except (OSError, ValueError) as exc:
        output.unlink(missing_ok=True)
        return jsonify(error=str(exc)), 413
    with lock:
        job.update(status='done', progress=100, message='Arquivo processado no Windows!',
                   filename=output.name, final_path=str(output), output_count=1,
                   finished_at=datetime.now(timezone.utc))
        if active_job == jid:
            active_job = None
    log_job(job)
    return jsonify(ok=True)


@app.post('/api/login')
def login():
    if not ACCESS_KEY:
        return jsonify(error='O administrador deve configurar ACCESS_KEY no Render.'), 503
    ip = request.remote_addr or 'unknown'
    with lock:
        dq = login_attempts.setdefault(ip, deque())
        while dq and dq[0] < time.time() - 3600:
            dq.popleft()
        if len(dq) >= 12:
            return jsonify(error='Muitas tentativas de acesso. Tente mais tarde.'), 429
        dq.append(time.time())
    data = request.get_json(silent=True) or {}
    candidate = str(data.get('password', ''))[:200]
    if not hmac.compare_digest(candidate, ACCESS_KEY):
        return jsonify(error='Senha incorreta.'), 401
    session.clear()
    session.permanent = True
    session['authorized'] = True
    return jsonify(ok=True)


@app.post('/api/logout')
def logout():
    session.clear()
    return jsonify(ok=True)


def job_public(job: dict) -> dict:
    return {key: job[key] for key in ('id', 'status', 'mode', 'media_format', 'platform', 'message', 'progress', 'output_count', 'source_count', 'filename')}


def reserve_job(*, mode: str, media_format: str, platform: str, quality: str, links: list[str], source_name: str = '') -> dict:
    global active_job
    with lock:
        if active_job and jobs.get(active_job, {}).get('status') in {'queued', 'running'}:
            raise RuntimeError('Outro arquivo está sendo preparado. Tente novamente após concluir.')
        if len(recent_jobs) >= 8:
            raise RuntimeError('Limite de oito tarefas por hora atingido neste servidor.')
        jid = str(uuid.uuid4())
        directory = str(BASE / jid)
        Path(directory).mkdir(parents=True, exist_ok=True)
        job = dict(id=jid, status='queued', mode=mode, links=links,
                   media_format=media_format, platform=platform, quality=quality,
                   progress=0, message='Aguardando...', output_count=0,
                   source_count=1 if mode != 'playlist' else (MAX_PLAYLIST_MP3 if media_format == 'mp3' else MAX_PLAYLIST_MP4),
                   filename='', created_ts=time.time(), directory=directory,
                   final_path='', finished_at=None, error=None, source_name=source_name)
        jobs[jid] = job
        active_job = jid
        recent_jobs.append(time.time())
    return job


@app.post('/api/jobs')
@requires_auth
def create_job():
    prune()
    data = request.get_json(silent=True) or {}
    if request.content_length and request.content_length > 12_000:
        return jsonify(error='Pedido muito grande.'), 413
    mode = data.get('mode')
    media_format = data.get('media_format')
    platform = data.get('platform')
    quality = str(data.get('quality'))
    engine = data.get('engine', 'server')
    if engine not in {'server', 'bridge'}:
        return jsonify(error='Motor invalido.'), 400
    if engine == 'bridge':
        with lock:
            if not bridge_token or time.time() - bridge_seen_at >= BRIDGE_HEARTBEAT_SECONDS:
                return jsonify(error='O computador nao esta conectado. Abra INICIAR_PONTE_WEB.bat e conecte-o antes.'), 409
    if mode not in {'videos', 'playlist'} or media_format not in {'mp3', 'mp4'}:
        return jsonify(error='Escolha vídeo ou playlist e MP3 ou MP4.'), 400
    if platform not in {'youtube', 'instagram', 'tiktok', 'spotify'}:
        return jsonify(error='Plataforma inválida.'), 400
    if data.get('permission') is not True:
        return jsonify(error='Confirme que o conteúdo é seu ou que possui autorização para baixar.'), 400
    if quality not in ({'128', '192', '320'} if media_format == 'mp3' else {'360', '720', '1080'}):
        return jsonify(error='Qualidade inválida.'), 400
    originals = data.get('links')
    if not isinstance(originals, list) or len(originals) != 1:
        return jsonify(error='Cole um link de vídeo ou playlist por vez.'), 400
    try:
        link = normalized_link(originals[0], platform, mode == 'playlist')
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400
    prune()
    try:
        job = reserve_job(mode=mode, media_format=media_format, platform=platform, quality=quality, links=[link])
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 429
    job['engine'] = engine
    log_job(job)
    if engine == 'server':
        threading.Thread(target=process_job, args=(job['id'],), daemon=True).start()
    else:
        with lock:
            job['message'] = 'Aguardando o programa no Windows...'
    return jsonify(job=job_public(job)), 202


@app.post('/api/upload')
@requires_auth
def convert_own_file():
    """Legitimate alternative to exporting media from Spotify streaming."""
    prune()
    if request.form.get('permission') != 'true':
        return jsonify(error='Confirme que você tem direitos sobre o arquivo.'), 400
    media_format = request.form.get('media_format')
    quality = request.form.get('quality')
    if media_format not in {'mp3', 'mp4'} or quality not in ({'128','192','320'} if media_format == 'mp3' else {'360','720','1080'}):
        return jsonify(error='Formato ou qualidade inválidos.'), 400
    upload = request.files.get('file')
    if upload is None or not upload.filename:
        return jsonify(error='Selecione um arquivo próprio.'), 400
    original = secure_filename(upload.filename)
    if Path(original).suffix.lower() not in UPLOAD_EXTS:
        return jsonify(error='Formato de arquivo não aceito.'), 400
    if media_format == 'mp4' and Path(original).suffix.lower() not in {'.mp4', '.mov', '.webm'}:
        return jsonify(error='Para gerar MP4, envie um arquivo de vídeo (.mp4, .mov ou .webm).'), 400
    try:
        job = reserve_job(mode='videos', media_format=media_format, platform='arquivo', quality=quality, links=[], source_name=safe_name(Path(original).stem))
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 429
    path = Path(job['directory']) / ('_source_input' + Path(original).suffix.lower())
    try:
        upload.save(path)
        if path.stat().st_size > MAX_SINGLE_BYTES:
            raise ValueError('O arquivo enviado supera 85 MB.')
    except Exception:
        with lock:
            global active_job
            if active_job == job['id']:
                active_job = None
            jobs.pop(job['id'], None)
        shutil.rmtree(job['directory'], ignore_errors=True)
        return jsonify(error='Arquivo excede o limite de 85 MB ou não pôde ser salvo.'), 413
    job['input_path'] = str(path)
    log_job(job)
    threading.Thread(target=process_job, args=(job['id'],), daemon=True).start()
    return jsonify(job=job_public(job)), 202


def process_job(jid: str) -> None:
    global active_job
    job = jobs[jid]
    folder = Path(job['directory'])
    try:
        with lock:
            job.update(status='running', progress=3, message='Preparando mídia...')
        log_job(job)
        if job['platform'] == 'arquivo':
            convert_local(job, folder)
            title = 'Arquivo próprio'
        else:
            title = extract_online(job, folder)
        ext = '.' + job['media_format']
        source_input = Path(job['input_path']) if job['platform'] == 'arquivo' else None
        files = sorted((p for p in folder.glob('*' + ext) if p != source_input), key=lambda p: p.name.casefold())
        if not files:
            raise RuntimeError('Nenhum arquivo compatível foi obtido. Verifique a disponibilidade pública e as permissões.')
        count_cap = (MAX_PLAYLIST_MP3 if job['media_format'] == 'mp3' else MAX_PLAYLIST_MP4) if job['mode'] == 'playlist' else 1
        if len(files) > count_cap:
            raise RuntimeError('A quantidade de arquivos excede o limite.')
        if sum(f.stat().st_size for f in files) > MAX_FINAL_BYTES:
            raise RuntimeError('Arquivos excedem o limite total de 250 MB do servidor.')
        with lock:
            job.update(progress=92, message='Organizando arquivos...', output_count=len(files))
        if job['mode'] == 'videos':
            final = files[0]
        else:
            folder_name = safe_name(title or 'Playlist', 'Playlist')
            final = folder / (folder_name + '.zip')
            with zipfile.ZipFile(final, 'w', compression=zipfile.ZIP_STORED) as z:
                for index, file in enumerate(files, start=1):
                    normalized = safe_name(file.stem, f'Faixa {index:03d}')
                    z.write(file, arcname=f'{folder_name}/{normalized}{ext}')
            for file in files:
                file.unlink(missing_ok=True)
        with lock:
            job.update(status='done', progress=100, message='Arquivo pronto para baixar!', filename=final.name,
                       final_path=str(final), finished_at=datetime.now(timezone.utc))
    except Exception as exc:
        # Descreve recusas do serviço sem sugerir que o usuário compartilhe
        # cookies de sessão ou outras credenciais de sua conta Google.
        raw_error = str(exc)
        lowered = raw_error.lower()
        youtube_verification = job['platform'] == 'youtube' and any(
            phrase in lowered for phrase in (
                'sign in to confirm', 'not a bot', 'login_required',
                'cookies-from-browser', 'confirm you\u2019re not a bot'
            )
        )
        if job['platform'] == 'youtube':
            category = classify_youtube_diagnostic(raw_error)
            public_error = DIAGNOSTIC_MESSAGES[category]
        else:
            public_error = raw_error[:220]
        app.logger.warning(
            'Falha no processamento (%s; verificacao_youtube=%s)',
            type(exc).__name__, youtube_verification
        )
        with lock:
            job.update(status='error', message='Não foi possível preparar este arquivo.',
                       error=public_error, finished_at=datetime.now(timezone.utc))
        shutil.rmtree(folder, ignore_errors=True)
    finally:
        with lock:
            if active_job == jid:
                active_job = None
        log_job(job)


def extract_online(job: dict, folder: Path) -> str:
    import yt_dlp
    mode = job['mode']
    media = job['media_format']
    cap = MAX_PLAYLIST_MP3 if media == 'mp3' else MAX_PLAYLIST_MP4
    q = job['quality']

    def hook(state):
        if state.get('status') == 'downloading':
            total = state.get('total_bytes') or state.get('total_bytes_estimate')
            percent = int(100 * (state.get('downloaded_bytes') or 0) / total) if total else 0
            with lock:
                job['message'] = f'Transferindo vídeo/faixa: {percent}%'
                job['progress'] = min(78, max(job['progress'], 7 + percent // 2))
        elif state.get('status') == 'finished':
            with lock:
                job['message'] = f'Finalizando {media.upper()}...'
                job['progress'] = max(65, job['progress'])

    def filter_info(info, *, incomplete=False):
        duration = info.get('duration')
        if duration is not None and duration > MAX_DURATION:
            return 'Mídia longa demais (máximo 15 minutos).'
        if info.get('is_live') or info.get('live_status') == 'is_live':
            return 'Transmissões ao vivo não são aceitas.'
        return None

    opts = {
        'format': ('bestaudio/best' if media == 'mp3'
                   else f'bv*[height<={q}][ext=mp4]+ba[ext=m4a]/b[height<={q}][ext=mp4]/bv*[height<={q}]+ba/b'),
        'outtmpl': str(folder / ('%(playlist_index)03d - %(title).140B [%(id)s].%(ext)s' if mode == 'playlist'
                                  else '%(title).140B [%(id)s].%(ext)s')),
        'noplaylist': mode != 'playlist',
        'playlist_items': f'1:{cap}' if mode == 'playlist' else None,
        'ignoreerrors': mode == 'playlist',
        'quiet': True,
        'no_warnings': True,
        'noprogress': True,
        'max_filesize': MAX_SINGLE_BYTES,
        'socket_timeout': 18,
        'retries': 2,
        'fragment_retries': 2,
        'match_filter': filter_info,
        'progress_hooks': [hook],
        'restrictfilenames': False,
        'merge_output_format': 'mp4',
    }
    if media == 'mp3':
        opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': q}]
    else:
        # FFmpeg converts WebM/etc to MP4 when the provider lacks an MP4 stream.
        opts['postprocessors'] = [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}]
    with yt_dlp.YoutubeDL(opts) as downloader:
        info = downloader.extract_info(job['links'][0], download=True)
    if mode == 'playlist' and isinstance(info, dict):
        return safe_name(info.get('title') or 'Minha Playlist')
    return 'Vídeo'


def convert_local(job: dict, folder: Path) -> None:
    import subprocess
    src = Path(job['input_path'])
    out = folder / ('Wave - ' + safe_name(job['source_name'], 'Meu arquivo') + '.' + job['media_format'])
    if job['media_format'] == 'mp3':
        command = ['ffmpeg', '-nostdin', '-y', '-loglevel', 'error', '-i', str(src),
                   '-vn', '-codec:a', 'libmp3lame', '-b:a', job['quality'] + 'k',
                   '-t', str(MAX_DURATION), str(out)]
    else:
        height = job['quality']
        command = ['ffmpeg', '-nostdin', '-y', '-loglevel', 'error', '-i', str(src),
                   '-vf', f'scale=-2:min(ih\\,{height})', '-codec:v', 'libx264',
                   '-preset', 'veryfast', '-crf', '25', '-codec:a', 'aac',
                   '-b:a', '128k', '-movflags', '+faststart', '-t', str(MAX_DURATION), str(out)]
    with lock:
        job.update(message='Convertendo arquivo enviado...', progress=46)
    subprocess.run(command, check=True, timeout=MAX_DURATION+100, capture_output=True)
    with lock:
        job.update(progress=85, message='Conversão concluída!')


@app.get('/api/jobs/<jid>')
@requires_auth
def get_job(jid: str):
    prune()
    with lock:
        job = jobs.get(jid)
        if not job:
            return jsonify(error='Tarefa não encontrada ou expirada.'), 404
        public = job_public(job)
        if job['status'] == 'error':
            public['error'] = job.get('error')
    return jsonify(job=public)


@app.get('/api/jobs/<jid>/download')
@requires_auth
def download(jid: str):
    prune()
    with lock:
        job = jobs.get(jid)
        if not job or job['status'] != 'done':
            return jsonify(error='Arquivo não está pronto ou expirou.'), 404
        path = Path(job['final_path'])
    if not path.is_file():
        return jsonify(error='O arquivo expirou. Inicie outra conversão.'), 404
    response = send_file(path, as_attachment=True, download_name=job['filename'], max_age=0)
    response.cache_control.no_store = True
    return response


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', '5000')), debug=False)
