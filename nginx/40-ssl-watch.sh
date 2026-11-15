#!/bin/sh
# HTTPS 설정은 인증서가 있을 때만 켠다 (처음 띄울 때는 인증서가 없어 nginx가 시작하지 못하는 것을 막음).
# 그 뒤 1분마다 인증서 파일을 확인해, 처음 발급되거나 갱신되어 바뀌면 HTTPS를 켜고 nginx 설정을 다시 읽힌다.
set -eu

CERT="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"

apply() {
    if [ -f "$CERT" ]; then
        envsubst '${DOMAIN}' < /etc/nginx/https.conf.in > /etc/nginx/conf.d/https.conf
    else
        rm -f /etc/nginx/conf.d/https.conf
    fi
}

mtime() { stat -L -c %Y "$CERT" 2>/dev/null || echo 0; }

apply
if [ -f "$CERT" ]; then echo "[ssl] 인증서 있음: HTTPS 사용 (${DOMAIN})"; else echo "[ssl] 인증서 없음: 발급될 때까지 HTTP만 사용"; fi

(
    last=$(mtime)
    while :; do
        sleep 60
        now=$(mtime)
        if [ "$now" != "$last" ]; then
            apply
            if nginx -t -q; then
                nginx -s reload
                echo "[ssl] 인증서가 바뀌어 nginx 설정을 다시 읽음 (${DOMAIN})"
                last=$now
            fi
        fi
    done
) &
