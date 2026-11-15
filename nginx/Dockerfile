# 앞단 Nginx: HTTP(80)는 Let's Encrypt 확인 파일과 https 이동, HTTPS(443)는 조회 서버(app)로 전달
FROM nginx:1.28-alpine

COPY default.conf /etc/nginx/conf.d/default.conf
COPY proxy_app.conf /etc/nginx/proxy_app.conf
COPY https.conf.in /etc/nginx/https.conf.in
COPY 40-ssl-watch.sh /docker-entrypoint.d/40-ssl-watch.sh
# 윈도우에서 편집해 줄바꿈이 CRLF가 되어도 동작하도록
RUN sed -i 's/\r$//' /docker-entrypoint.d/40-ssl-watch.sh /etc/nginx/https.conf.in /etc/nginx/proxy_app.conf /etc/nginx/conf.d/default.conf \
 && chmod +x /docker-entrypoint.d/40-ssl-watch.sh
