# Wave Downloder — MP3 e MP4

Site web para organizar e converter arquivos próprios ou conteúdos cuja cópia esteja autorizada.

**Interface:** primeiro escolha MP3 ou MP4; depois YouTube, Instagram, TikTok ou Spotify; em seguida selecione vídeo ou playlist, qualidade e link.

- **YouTube:** vídeo MP3/MP4, playlist em ZIP (limites de 20 MP3 ou 8 MP4).
- **Instagram / TikTok:** vídeos de URLs públicas sem login.
- **Spotify:** não exporta músicas do streaming; oferece conversão de um arquivo próprio enviado pelo usuário.
- **MP3:** 128, 192 ou 320 kbps; **MP4:** até 360p, 720p ou 1080p.
- Senha obrigatória, limite de tarefas e arquivos temporários.

## Hospedagem
Render usa Docker, Flask, FFmpeg, Deno e yt-dlp. Neon PostgreSQL é opcional para histórico de tarefas. Configure no ambiente Render: `ACCESS_KEY` (obrigatória), `SESSION_SECRET` e `DATABASE_URL` (não coloque senhas no GitHub).

A versão inicial foi transferida em `source.tar.xz` (código completo compactado). Um GitHub Action tenta expandir seus arquivos para a raiz. O Dockerfile funciona com o pacote mesmo se esse Action não executar.

Saúde: `GET /health`. O funcionamento de downloads depende de disponibilidade e permissões das plataformas. O plano Free do Render pode interromper conversões grandes.
