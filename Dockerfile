FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DENO_INSTALL=/opt/deno
ENV PATH="/opt/deno/bin:${PATH}"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates unzip xz-utils \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSL https://deno.land/install.sh | sh \
 && deno --version
WORKDIR /app
COPY source.tar.xz ./source.tar.xz
RUN tar -xJf source.tar.xz && rm -f source.tar.xz
RUN pip install --no-cache-dir -r requirements.txt
ENV APP_ENV=production PORT=10000
EXPOSE 10000
CMD ["sh","-c","exec gunicorn --bind 0.0.0.0:${PORT:-10000} --workers 1 --threads 4 --timeout 120 app:app"]
