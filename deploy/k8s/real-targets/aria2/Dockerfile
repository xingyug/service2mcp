FROM alpine:3.20

RUN apk add --no-cache aria2 curl

COPY aria2.conf /etc/aria2/aria2.conf
RUN mkdir -p /downloads /etc/aria2

EXPOSE 6800

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD curl -sf -d '{"jsonrpc":"2.0","method":"aria2.getVersion","id":"hc","params":["token:test-secret"]}' \
      -H 'Content-Type: application/json' http://127.0.0.1:6800/jsonrpc || exit 1

CMD ["aria2c", "--conf-path=/etc/aria2/aria2.conf"]
