# Wave Downloder V5 - ponte local segura para o site.
# Copie para a pasta da V4, junto a yt-dlp.exe, deno.exe, ffmpeg.exe e ffprobe.exe.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = 'https://wave-downloder.onrender.com'
$yt = Join-Path $root 'yt-dlp.exe'
$deno = Join-Path $root 'deno.exe'
$ffmpeg = Join-Path $root 'ffmpeg.exe'
$ffprobe = Join-Path $root 'ffprobe.exe'
foreach($file in @($yt,$deno,$ffmpeg,$ffprobe)){if(-not (Test-Path -LiteralPath $file -PathType Leaf)){Write-Host "Ausente: $file" -ForegroundColor Red; exit 1}}
$env:PATH = $root + [IO.Path]::PathSeparator + $env:PATH
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Write-Host ''
Write-Host 'WAVE DOWNLODER V5 - PONTE WEB PARA O SEU WINDOWS' -ForegroundColor Cyan
Write-Host 'O motor V4 roda aqui. A pagina web envia tarefas autorizadas por HTTPS.' -ForegroundColor Gray
Write-Host ''
$token = (Read-Host 'Cole o codigo gerado pelo site').Trim()
if($token.Length -lt 30){Write-Host 'Codigo invalido.' -ForegroundColor Red;exit 1}
$headers = @{ 'X-Wave-Bridge' = $token }
$work = Join-Path $root 'Downloads_Web'
New-Item -ItemType Directory -Path $work -Force | Out-Null

function Send-Progress([string]$id,[int]$pct){
  try { Invoke-RestMethod -Uri "$server/api/bridge/progress" -Headers $headers -Method POST -ContentType 'application/json' -Body (@{id=$id;progress=$pct}|ConvertTo-Json -Compress) -TimeoutSec 15 | Out-Null } catch {}
}
function Send-Failure([string]$id){
  try { Invoke-RestMethod -Uri "$server/api/bridge/failure" -Headers $headers -Method POST -ContentType 'application/json' -Body (@{id=$id}|ConvertTo-Json -Compress) -TimeoutSec 20 | Out-Null } catch {}
}
function Handle-Job($job){
  $id = [string]$job.id
  if($id -notmatch '^[0-9a-f-]{36}$'){throw 'ID recebido invalido.'}
  $folder = Join-Path $work $id
  New-Item -ItemType Directory -Path $folder -Force | Out-Null
  try{
    $mode=[string]$job.mode; $media=[string]$job.media_format; $quality=[string]$job.quality
    $url=[string]$job.url
    if($url -notmatch '^https://(www\.youtube\.com/|www\.instagram\.com/|www\.tiktok\.com/|vm\.tiktok\.com/|vt\.tiktok\.com/)'){throw 'Origem nao permitida.'}
    if($mode -notin @('videos','playlist') -or $media -notin @('mp3','mp4')){throw 'Configuracao invalida.'}
    if($quality -notin @('128','192','320','360','720','1080')){throw 'Qualidade invalida.'}
    Write-Host ("Tarefa: "+$id+" | "+$media.ToUpperInvariant()) -ForegroundColor Cyan
    Send-Progress $id 10
    $argv=@('--no-cache-dir','--windows-filenames','--trim-filenames','90',
      '--socket-timeout','20','--retries','2','--max-filesize','85M',
      '--match-filter','duration <= 900 & !is_live',
      '--js-runtimes',('deno:'+$deno),'--ffmpeg-location',$root,
      '--paths',$folder,
      '--output','%(title).90s [%(id)s].%(ext)s',
      '--newline')
    if($mode -eq 'playlist'){$argv+=@('--yes-playlist','--playlist-end',$(if($media -eq 'mp3'){'20'}else{'8'}))}
    else{$argv+='--no-playlist'}
    if($media -eq 'mp3'){
      $argv+=@('--format','bestaudio/best','--extract-audio','--audio-format','mp3','--audio-quality',($quality+'K'))
    }else{
      $fmt="bv*[height<=$quality][ext=mp4]+ba[ext=m4a]/b[height<=$quality][ext=mp4]/bv*[height<=$quality]+ba/b"
      $argv+=@('--format',$fmt,'--merge-output-format','mp4','--remux-video','mp4')
    }
    $argv+=@('--',$url)
    & $yt @argv
    if($LASTEXITCODE -ne 0){throw 'yt-dlp terminou com erro; consulte as mensagens acima.'}
    Send-Progress $id 78
    $ext='.'+$media
    $files=@(Get-ChildItem -LiteralPath $folder -File | Where-Object {$_.Extension.ToLowerInvariant() -eq $ext})
    if($files.Count -lt 1){throw 'Nenhum arquivo gerado.'}
    $final=$files[0]
    if($mode -eq 'playlist'){
      Add-Type -AssemblyName System.IO.Compression.FileSystem
      $zipPath=Join-Path $folder 'Playlist Wave.zip'
      $z=[IO.Compression.ZipFile]::Open($zipPath,[IO.Compression.ZipArchiveMode]::Create)
      try{foreach($f in $files){[IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z,$f.FullName,$f.Name) | Out-Null}}
      finally{$z.Dispose()}
      $final=Get-Item -LiteralPath $zipPath
    }
    if($final.Length -gt 85MB){throw 'Arquivo excede 85 MB.'}
    Send-Progress $id 90
    Write-Host 'Enviando resultado para o site...' -ForegroundColor Cyan
    $result=& curl.exe --silent --show-error --fail --max-time 480 -X POST -H ("X-Wave-Bridge: "+$token) -F ("id="+$id) -F ("file=@"+$final.FullName) "$server/api/bridge/result"
    if($LASTEXITCODE -ne 0){throw 'Falha ao enviar para o site.'}
    Write-Host 'Concluido! O botao de download vai aparecer no site.' -ForegroundColor Green
  }catch{
    Write-Host ("Erro: "+$_.Exception.Message) -ForegroundColor Red
    Send-Failure $id
  }finally{
    Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
  }
}
Write-Host ''
Write-Host 'Conectando... Deixe esta janela aberta para processar tarefas.' -ForegroundColor Cyan
while($true){
  try{
    $reply=Invoke-RestMethod -Uri "$server/api/bridge/next" -Method POST -Headers $headers -TimeoutSec 35
    if($reply -and $reply.job){Handle-Job $reply.job}
    Start-Sleep -Seconds 4
  }catch{
    $status=0
    try{$status=[int]$_.Exception.Response.StatusCode}catch{}
    if($status -eq 403){Write-Host 'Codigo recusado. Gere um novo codigo no site e reinicie a ponte.' -ForegroundColor Red;break}
    Write-Host 'Conexao temporariamente indisponivel; tentando de novo...' -ForegroundColor Yellow
    Start-Sleep -Seconds 9
  }
}
