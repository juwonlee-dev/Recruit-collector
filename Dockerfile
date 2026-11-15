# 조회 서버(serve) 이미지. 자동 수집(스케줄러)도 이 서버 안에서 돈다.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Seoul

WORKDIR /app

# 이력서 PDF(WeasyPrint)에 필요한 글자 배치 라이브러리(Pango)와 한글 글꼴(Noto Sans CJK)
RUN apt-get update  && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-noto-cjk  && rm -rf /var/lib/apt/lists/*

# 패키지 설치 (템플릿·정적 파일 포함). 설치 후 원본 소스는 지운다
COPY pyproject.toml ./
COPY recruit_collector ./recruit_collector
RUN pip install --no-cache-dir . && rm -rf recruit_collector

# config.yaml과 config/ 폴더는 이미지에 넣지 않고 docker-compose.yml에서 연결한다 (비밀값·화면에서 바꾸는 파일)
RUN useradd --create-home --uid 1000 app && chown app /app
USER app

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/robots.txt', timeout=4)"

CMD ["recruit-collector", "--config", "/app/config.yaml", "serve", "--host", "0.0.0.0", "--port", "8765"]
