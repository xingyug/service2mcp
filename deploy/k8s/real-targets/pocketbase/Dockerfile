FROM alpine:3.20

ARG PB_VERSION=0.25.0

RUN apk add --no-cache ca-certificates wget unzip curl && \
    wget -q -O /tmp/pb.zip \
      "https://github.com/pocketbase/pocketbase/releases/download/v${PB_VERSION}/pocketbase_${PB_VERSION}_linux_amd64.zip" && \
    unzip /tmp/pb.zip -d /pb && \
    rm /tmp/pb.zip && \
    chmod +x /pb/pocketbase

WORKDIR /pb

EXPOSE 8090

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s \
  CMD curl -sf http://127.0.0.1:8090/api/health || exit 1

COPY entrypoint.sh /pb/entrypoint.sh
RUN chmod +x /pb/entrypoint.sh

CMD ["/pb/entrypoint.sh"]
